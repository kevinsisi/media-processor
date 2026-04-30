# SMB Share Verification (Mac ↔ Windows)

## Goal
Confirm Mac can mount Windows SMB share and read/write a 5GB test file.

## Windows host (developer) setup

1. Create the share folder: `mkdir C:\MediaProcessor\assets`
2. Right-click the folder → Properties → Sharing tab → Advanced Sharing
3. Tick "Share this folder", set Share name = `MediaProcessor`
4. Permissions → Add the user account that the Mac will authenticate as → grant "Change" + "Read"
5. Confirm Windows firewall allows File and Printer Sharing on Private network:
   ```powershell
   Get-NetFirewallRule -DisplayGroup "File and Printer Sharing" |
     Where-Object Enabled -eq True | Format-Table Name, DisplayName, Profile
   ```
6. Check Windows IP on the LAN: `ipconfig` → note the LAN IPv4 (e.g. `192.168.1.50`)

## Mac (girlfriend) verification

1. Finder → "前往" → "連線到伺服器" → enter `smb://192.168.1.50/MediaProcessor`
2. Authenticate with the Windows user credentials
3. Folder appears in Finder. Check it's writable: drag a small file in.
4. Time a 5GB file copy:
   ```bash
   # On Mac terminal
   mkfile -n 5g /tmp/test_5gb.bin
   time cp /tmp/test_5gb.bin /Volumes/MediaProcessor/test_5gb.bin
   ```
5. Record observed throughput (e.g. 110 MB/s on gigabit LAN).

## Acceptance criteria
- [ ] Mac mounts the share without errors
- [ ] Mac can read and write files
- [ ] 5GB transfer completes; record actual MB/s in this checklist
- [ ] Mac can re-mount the share after restart (Finder remembers credentials)

## WSL2 mount (Windows host side)
The Docker containers will read assets via the WSL2 mount of the same path:
```bash
ls /mnt/c/MediaProcessor/assets
```
Confirm the same files dropped from Mac appear under `/mnt/c/MediaProcessor/assets`.

## Result log

Record the verification date and observed throughput here:

```
Date: _____________
Mac → Windows transfer: ___ MB/s
WSL2 mount path access: PASS / FAIL
Notes: _____________
```
