## AdProcess Sync: Before vs After
> **Scheduler note:** “Every minute” means **every cycle**. The next cycle starts ~1 minute after the previous one **finishes**. Depending on how much needs copying from the Cloud, a cycle can be as short as ~1 minute or as long as ~5 minutes.


### Previous (legacy) flow
    Every minute:
      SyncConfigs()
      Detect "mode" from first entry (dir vs file)
      If dir mode:
        In-place copy files; delete stale in live dir
        (FEH may see half-updated set)
      If file mode:
        Copy cloud → SD .tmp → replace
      Continue scanning entries (could do many writes/min)

**Pain points:**
- Mode coupling (first entry dictates behavior)
- In-place slideshow updates → partial sets visible
- Multiple writes per minute → SD wear
- Weak/no logging on failures

### New flow (current)
    Every minute:
      SyncConfigs()
      For each PLAY_LIST entry in order:
        If entry is a DIR (endswith "/"):
          updated = SyncDir(entry)
          If updated: STOP (only 1 sync per call)
          Else: continue
        Else (entry is a FILE):
          Decide if video needs update (size/mtime)
          If yes:
            Cloud → RAM (/dev/shm) .part
            RAM .part → SD .tmp → atomic replace
            If currently being played & open: restart player
            STOP (only 1 sync per call)
          Else: continue

**What `SyncDir()` does now:**
    Per slideshow entry:
      Guard: require cloud dir & local base exist
      ram_tmp_dir := /dev/shm/AdProcess-sync/<ShowName>  (RAM)
      Build RAM tmp from cloud file list (flat):
        For each cloud file:
          If local exists and is strictly newer (mtime +1s), use local
          Else use cloud (mark: copied_from_cloud = True)
      Detect cloud-side deletions (first stray local file)
      If no copied_from_cloud AND no deletions:
        Delete RAM tmp; return False (no-op)
      Else (we have a change to apply):
        sd_tmp_dir := <live dir>.tmpdir (on SD; same FS as live)
        Copy RAM tmp → sd_tmp_dir
        If show is currently playing and player is open: StopPlayer()
        Rename swap on SD (metadata-cheap):
          <live dir> → <live dir>.old
          sd_tmp_dir  → <live dir>
        Cleanup RAM tmp, sd_tmp_dir, and .old
        If it was playing: PlayVideo(<live dir>)
        return True

**Path roles (why each exists):**
- `src_dir` — Cloud source (read-only); defines the authoritative file list (flat).
- `dst_dir` — Live local slideshow on SD; what the player reads; never edited in-place.
- `ram_tmp_dir` — Per-show staging in RAM; avoids SD writes during build; always deleted.
- `sd_tmp_dir` — SD temp (same filesystem as `dst_dir`) so the final rename swap is atomic.
- `old_dir` — Brief parking spot for the current live dir during the swap; removed immediately.

**Key behavioral changes:**
- **No mode logic**: each entry stands alone.
- **One-and-done per minute**: the first actual change wins; others wait for the next tick.
- **Slideshow updates are atomic** via SD rename swap; no more half-sets.
- **Video updates RAM-stage first**, then SD atomic replace.
- **Purges honored**: removing a slide in Cloud removes it locally on the next sync.
- **No silent failures**: all exceptions logged with context.
- **Minimal SD wear**: nothing touches SD unless content truly changes.

**Operational notes:**
- RAM tmp is per-show/per-file and **always cleaned up**; memory returns to baseline after each call.
- FEH with `--reload 1` refreshes slides after swap; restart only when the file list changes (handled).
- Cycles are scheduled **one minute after completion**, so no overlap/races.

---

## Post-2.00 Punch List (future you snacks)
- Extract `MTIME_SLOP = 1` as a module constant (avoid sprinkling `+ 1`).
- Optional: auto-fallback to SD tmp if slideshow size won’t fit comfortably in `/dev/shm`.
- Optional: manifest-driven sync (size/mtime/hash) to eliminate even SD-temp rewrites of unchanged files.
- Optional: tiny “dry run” flag to log planned copies/deletes without touching the SD.
- Optional: consolidate logging levels (INFO for state changes, DEBUG for per-file details) to keep logs lean.
