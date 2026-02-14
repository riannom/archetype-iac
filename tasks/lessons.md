# Lessons Learned

## 2026-02-14: sed `a` command does NOT interpret `\t` as tab in single quotes

**Bug**: `sed -i '/pattern/a\\ttext' file` produces literal `\t` instead of a tab character.

**Impact**: LVM `global_filter` was written as `\tglobal_filter = ...` (invalid config key). LVM silently ignored it, leaving the system vulnerable to the NBD/LVM crash cascade. This caused repeated system hangs on agent-01 requiring power cycles.

**Fix**: Use `$'...'` ANSI-C quoting: `sed -i $'/pattern/a\\\\\ttext' file` to get a real tab.

**Rule**: Always verify config changes take effect. For LVM, run `lvm dumpconfig <setting>` after writing to `lvm.conf`. Never trust `grep` alone to validate config correctness.

## 2026-02-14: QEMU writeback cache mode corrupts page cache of backing images

**Bug**: QEMU with default `writeback` cache mode (`cache.direct=false`) modifies the host page cache of read-only qcow2 backing images during COW operations. The file on disk is fine, but any process reading the file (including QEMU itself) gets corrupted data from the page cache.

**Symptoms**: Unstable MD5/SHA256 hashes of the backing image (changes between reads), I/O errors in QEMU logs, VM boot failures due to corrupted data (e.g., RPM digest failures in XRv9000). Hashes stabilize only after killing QEMU + `echo 3 > /proc/sys/vm/drop_caches`.

**Fix**: Use `cache='none'` (O_DIRECT) on all libvirt disk driver elements to bypass the page cache entirely. Also add `io='native'` for optimal AIO performance with O_DIRECT.

**Rule**: Always use `cache='none'` for QEMU disks backed by qcow2 overlays. The default `writeback` mode is unsafe when multiple VMs share a backing image or when the backing image is on network storage. If you see unstable file hashes on a file that should be read-only, suspect page cache corruption before suspecting disk corruption.
