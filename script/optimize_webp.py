# Re-encodes animated .webp files at lossy quality 30 using Google's libwebp tools.
# Searches the parent directory of /script/ recursively for .webp files.
# Skips static webps. Only processes files newer than the last run.
#
# Options:
#   --all           Process all files, ignore timestamp
#   --skip N        Skip first N files
#   --large-files   Only target files >4.9 MB. Tries quality 20, then 10.
#
# Requires: Google libwebp (winget install Google.Libwebp)

import subprocess, os, re, tempfile, shutil, glob, time

QUALITY = "30"
QUALITY_LARGE_1 = "20"
QUALITY_LARGE_2 = "10"
SIZE_LIMIT = 4.9 * 1024 * 1024  # 4.9 MB
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
TIMESTAMP_FILE = os.path.join(SCRIPT_DIR, ".last_optimized")

def find_webp_bin():
	path = shutil.which("webpinfo")
	if path:
		return os.path.dirname(path)

	winget_root = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages")
	for match in glob.glob(os.path.join(winget_root, "Google.Libwebp*", "**", "bin", "webpinfo.exe"), recursive=True):
		return os.path.dirname(match)

	for candidate in [r"C:\libwebp\bin", os.path.expanduser("~/libwebp/bin"), "/usr/local/bin", "/usr/bin"]:
		if os.path.isfile(os.path.join(candidate, "webpinfo" + (".exe" if os.name == "nt" else ""))):
			return candidate

	return None

WEBP_BIN = find_webp_bin()
if not WEBP_BIN:
	print("libwebp not found. Install with: winget install Google.Libwebp")
	exit(1)

ext = ".exe" if os.name == "nt" else ""
webpinfo = os.path.join(WEBP_BIN, "webpinfo" + ext)
anim_dump = os.path.join(WEBP_BIN, "anim_dump" + ext)
img2webp = os.path.join(WEBP_BIN, "img2webp" + ext)
cwebp = os.path.join(WEBP_BIN, "cwebp" + ext)
webpmux = os.path.join(WEBP_BIN, "webpmux" + ext)

def get_webp_info(filepath):
	result = subprocess.run([webpinfo, filepath], capture_output=True, text=True)
	output = result.stdout
	is_animated = "Animation: 1" in output
	durations = [int(m) for m in re.findall(r"Duration:\s+(\d+)", output)]
	loop_match = re.search(r"Loop count\s*:\s*(\d+)", output)
	loop = loop_match.group(1) if loop_match else "0"
	return is_animated, durations, loop

def encode_webp(tmpdir, frames, durations, loop, quality):
	"""Encode frames to output.webp in tmpdir. Returns output path or None."""
	output_path = os.path.join(tmpdir, "output.webp")
	rel_frames = [os.path.basename(f) for f in frames]

	cmd = [img2webp, "-loop", loop]
	for frame, dur in zip(rel_frames, durations):
		cmd.extend(["-d", str(dur), "-lossy", "-q", quality, frame])
	cmd.extend(["-o", "output.webp"])

	cmd_len = sum(len(a) + 1 for a in cmd)
	if cmd_len < 30000:
		subprocess.run(cmd, capture_output=True, cwd=tmpdir)
	else:
		for i, frame in enumerate(frames):
			out = os.path.join(tmpdir, f"f{i:05d}.webp")
			subprocess.run([cwebp, "-q", quality, frame, "-o", out], capture_output=True)

		ps = os.path.join(tmpdir, "mux.ps1")
		with open(ps, "w") as f:
			f.write(f'$a = @(\n')
			for i, dur in enumerate(durations):
				f.write(f'  "-frame", "f{i:05d}.webp", "+{dur}",\n')
			f.write(f'  "-loop", "{loop}",\n')
			f.write(f'  "-o", "output.webp"\n')
			f.write(f')\n')
			f.write(f'& "{webpmux}" @a\n')
		subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File", ps],
			capture_output=True, cwd=tmpdir)

	if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
		return None
	return output_path


def optimize(filepath, large_files=False):
	is_animated, durations, loop = get_webp_info(filepath)
	if not is_animated:
		return None, "static"
	if not durations:
		return None, "no frames"

	orig_size = os.path.getsize(filepath)

	if large_files and orig_size < SIZE_LIMIT:
		return None, "under limit"

	tmpdir = tempfile.mkdtemp(prefix="webp_opt_")
	try:
		subprocess.run([anim_dump, filepath], capture_output=True, cwd=tmpdir)

		frames = sorted(glob.glob(os.path.join(tmpdir, "dump_*.png")))
		if not frames:
			return None, "dump failed"

		count = min(len(frames), len(durations))
		frames = frames[:count]
		durations = durations[:count]

		def clean_tmp():
			for f in glob.glob(os.path.join(tmpdir, "output.webp")):
				os.remove(f)
			for f in glob.glob(os.path.join(tmpdir, "f*.webp")):
				os.remove(f)
			for f in glob.glob(os.path.join(tmpdir, "mux.ps1")):
				os.remove(f)

		if large_files:
			# Step 1: Try quality 20.
			print(" [20%]", end="", flush=True)
			output_path = encode_webp(tmpdir, frames, durations, loop, QUALITY_LARGE_1)

			# Step 2: Try quality 10.
			if not output_path or os.path.getsize(output_path) > SIZE_LIMIT:
				print(" [10%]", end="", flush=True)
				clean_tmp()
				output_path = encode_webp(tmpdir, frames, durations, loop, QUALITY_LARGE_2)
		else:
			output_path = encode_webp(tmpdir, frames, durations, loop, QUALITY)

		if not output_path or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
			return None, "encode failed"

		new_size = os.path.getsize(output_path)
		if new_size >= orig_size:
			return None, f"larger ({new_size/1024/1024:.1f} MB vs {orig_size/1024/1024:.1f} MB)"

		shutil.copy2(output_path, filepath)
		return (orig_size, new_size), None
	finally:
		shutil.rmtree(tmpdir, ignore_errors=True)

def get_last_run_time():
	if os.path.exists(TIMESTAMP_FILE):
		return os.path.getmtime(TIMESTAMP_FILE)
	return 0

def save_last_run_time():
	with open(TIMESTAMP_FILE, "w") as f:
		f.write(str(time.time()))

def main():
	force_all = "--all" in os.sys.argv
	large_files = "--large-files" in os.sys.argv
	skip_n = 0
	for arg in os.sys.argv[1:]:
		if arg.startswith("--skip"):
			skip_n = int(arg.split("=")[1]) if "=" in arg else int(os.sys.argv[os.sys.argv.index(arg) + 1])

	last_run = 0 if (force_all or skip_n > 0) else get_last_run_time()

	if skip_n > 0:
		print(f"Skipping first {skip_n} files\n")
	elif last_run > 0:
		print(f"Only processing files newer than last run ({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last_run))})")
		print(f"Use --all to process everything\n")
	else:
		if not force_all:
			print("First run, processing all files\n")

	processed = 0
	skipped = 0
	skipped_old = 0
	total_saved = 0

	all_webp = sorted(glob.glob(os.path.join(ROOT_DIR, "**", "*.webp"), recursive=True))
	if skip_n > 0:
		webp_files = all_webp[skip_n:]
		skipped_old = skip_n
	else:
		webp_files = [f for f in all_webp if os.path.getmtime(f) > last_run]
		skipped_old = len(all_webp) - len(webp_files)
	total = len(webp_files)

	if skipped_old > 0:
		print(f"Found {len(all_webp)} .webp files, {skipped_old} unchanged, {total} to process\n")
	else:
		print(f"Found {total} .webp files\n")

	try:
		for i, filepath in enumerate(webp_files, 1):
			rel_path = os.path.relpath(filepath, ROOT_DIR)
			print(f"  [{i}/{total}] {rel_path}", end="", flush=True)

			sizes, error = optimize(filepath, large_files=large_files)
			if error:
				print(f" - SKIPPED ({error})")
				skipped += 1
			else:
				orig, new = sizes
				saved_kb = (orig - new) // 1024
				total_saved += (orig - new)
				pct = (orig - new) / orig * 100 if orig > 0 else 0
				print(f" - {orig/1024/1024:.1f} MB -> {new/1024/1024:.1f} MB (saved {saved_kb} KB, {pct:.0f}%)")
				processed += 1
	except KeyboardInterrupt:
		print("\n\nInterrupted.")

	print(f"\nProcessed: {processed}, Skipped: {skipped}")
	print(f"Total saved: {total_saved/1024/1024:.1f} MB")

	if processed > 0:
		save_last_run_time()

if __name__ == "__main__":
	main()
