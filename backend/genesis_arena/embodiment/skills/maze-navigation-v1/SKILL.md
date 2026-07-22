# Maze Navigation

Use the authoritative navigation memory as a depth-first exploration stack.

1. At each decision, choose an available passage from `current.untried` before revisiting one.
2. When no untried passage remains, choose `current.backtrack` until an unresolved junction is
   reached.
3. Prefer `follow_corridor` through a passage when precise single-cell positioning is unnecessary;
   the environment will stop the command at a junction, dead end, exit, or command limit.
4. After a failed or invalid action, re-read the current passages and movement receipt instead of
   repeating the same unavailable choice.
5. Use landmarks only to disambiguate revisited areas. Navigation memory, not the scratchpad, is
   authoritative for pose, explored passages, and backtracking.
6. Do not wait while a visible passage or valid backtrack is available.

Keep any private scratchpad note short and strategic. Never try to replace or rewrite the backend
navigation memory.
