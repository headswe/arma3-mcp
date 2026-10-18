# Arma 3 MCP

Lets an AI assistant (Claude Desktop, Claude Code or any other [MCP](https://modelcontextprotocol.io) client) work with a running Arma 3 game: run SQF and get the result back as JSON, inspect units, vehicles, groups, markers and config, request cropped map images, spawn and move things, change time and weather, and read the RPT log.

```
MCP client ──stdio──> arma3-mcp (Python) ──TCP 127.0.0.1:9120──> arma3_mcp_x64.dll ──callExtension──> @arma3_mcp addon (SQF)
                                                                    (inside the game)                    compile, call, toJSON
```

Three parts:

- **`@arma3_mcp` mod**: the extension (`arma3_mcp_x64.dll` / `.so`) opens a local TCP port inside the game process; the addon picks requests up, runs them with `compile` + `call`, and returns the result serialized with `toJSON`.
- **`arma3-mcp` MCP server** (`server/`): a Python MCP server that connects to that port and exposes the tools below.
- Large requests and results are split into pieces to fit callExtension's buffers, so a result can be megabytes.

Requires Arma 3 2.18 or newer (for `toJSON` / `fromJSON`) and Python 3.10+.

## Installing

### The mod

Every build on GitHub Actions uploads an `@arma3_mcp` artifact, the finished mod folder:

```
@arma3_mcp/
  addons/arma3_mcp_main.pbo
  arma3_mcp_x64.dll
  arma3_mcp_x64.so
  arma3_mcp.ini
```

- **Singleplayer / editor / hosting**: load it like any local mod (`-mod=@arma3_mcp`, or add it as a local mod in the launcher). Extensions that are not whitelisted are blocked by BattlEye, so start the game with BattlEye off.
- **Dedicated server**: `arma3server_x64.exe -serverMod=@arma3_mcp ...`. Clients need nothing installed.

When a mission starts, the RPT log says `[arma3_mcp] listening on 127.0.0.1:9120`. The bridge works while a mission runs: editor preview, singleplayer, a hosted game or a dedicated server. In the main menu and the editor itself (not previewing) nothing answers.

### The MCP server

With [uv](https://docs.astral.sh/uv/) nothing needs installing up front. Claude Code:

```sh
claude mcp add arma3 -- uvx --from "git+https://github.com/headswe/arma3-mcp#subdirectory=server" arma3-mcp
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "arma3": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/headswe/arma3-mcp#subdirectory=server", "arma3-mcp"]
    }
  }
}
```

Or from a checkout: `pip install ./server`, then use `arma3-mcp` as the command.

Options (each also has an environment variable):

| Option | Env | Default | |
|---|---|---|---|
| `--host` | `ARMA3_MCP_HOST` | `127.0.0.1` | where the extension listens |
| `--port` | `ARMA3_MCP_PORT` | `9120` | |
| `--token` | `ARMA3_MCP_TOKEN` | | must match the extension's token, if it has one |
| `--rpt-dir` | `ARMA3_RPT_DIR` | `%LOCALAPPDATA%\Arma 3` | where `.rpt` logs are; for a server, its `-profiles` folder |
| `--map-svg-dir` | `ARMA3_MAP_SVG_DIR` | `svg/tile-svg` | folder containing `diag_exportTerrainSvg` exports for `map_tile` |
| `--icon-dir` | `ARMA3_ICON_DIR` | `svg/icons` | folder containing extracted Arma PNG icons for map overlays |
| `--transport` | | `stdio` | or `streamable-http` / `sse` (with `--http-host`, `--http-port`) |

## Settings in the game

`@arma3_mcp/arma3_mcp.ini`, or the same names as `ARMA3_MCP_*` environment variables for the game process (they win over the file):

| Key | Default | |
|---|---|---|
| `host` | `127.0.0.1` | address to listen on |
| `port` | `9120` | |
| `token` | (none) | when set, clients must send it before anything else |
| `enabled` | `1` | `0` loads the mod without opening the port |
| `allow_client` | `0` | by default only the server (or singleplayer game) listens; `1` also listens on multiplayer clients |

**Anyone who can connect to the port can run any code in the game.** Keep `host` on `127.0.0.1`. If the MCP server has to run on another machine, set a `token` and only open the port to that machine.

## Tools

| Tool | What it does |
|---|---|
| `execute_sqf` | Runs SQF and returns the result as JSON. See below. |
| `game_status` | Whether the extension answers, whether a mission is running, queued/running requests. |
| `mission_info` | Mission, world, game version, time, date, weather, FPS, entity counts, the local player. |
| `list_players` | Name, UID, owner id, netId, side, group, role, position, vehicle, damage. |
| `list_units` | Units, filtered by side, distance to an object or position, class, players/AI, dead. |
| `list_vehicles` | Vehicles with crew, fuel and damage; same filters. |
| `list_groups` | Callsign, side, leader, unit counts, behaviour, combat mode, position, and waypoint details. |
| `get_object` | Everything about one object: class, parents, position, grid, crew, animation, loadout, variables. |
| `map_tile` | A cropped terrain image with current units, vehicles and markers, clipped to world-coordinate bounds. |
| `get_variable` / `set_variable` / `list_variables` | Variables in mission/ui/profile/parsing namespaces or on an object. |
| `get_config` | A config class (properties, including inherited ones, parents, subclasses) or a single property; `missionConfigFile` too. |
| `search_classes` | Find classes in CfgVehicles, CfgWeapons, ... by class or display name. |
| `create_object` | Spawn a vehicle, prop or unit at a position or in front of an object, optionally with crew. |
| `delete_object` | Delete an object (and its AI crew). Never deletes players. |
| `teleport` | Move an object or unit to a position or next to another object. |
| `send_message` | systemChat, hint or titleText to everyone, or a line in the RPT. |
| `set_environment` | Date, time of day, overcast, fog, rain, time multiplier (on the server). |
| `list_markers` / `create_marker` / `delete_marker` | Map markers. |
| `performance` | FPS, entity counts and the running scripts with their file and line. |
| `list_mods` | Loaded mods. |
| `read_rpt` / `list_rpt_files` | The end of the RPT log, filtered by regex or to script errors only. |

Objects are referred to by netId (`"2:345"`, as the list tools return them), a global variable name, or `"player"`.

### `map_tile`

`map_tile` returns a PNG image block for a bounded area of the current world. Coordinates are world metres: `top_left` is `[min_x, max_y]`, and `bottom_right` is `[max_x, min_y]`. For example:

```json
{"top_left": [3000, 6000], "bottom_right": [5000, 4000], "max_size": 1024}
```

The tool clips the world's `diag_exportTerrainSvg` export to those bounds, then overlays side-colored units, vehicles and map markers from the running mission. When an object's Arma config references a PAA icon, the server resolves that path to the matching PNG in the icon folder and embeds it in the map; directional marker icons are rotated according to their Arma marker heading. Overlays without a matching PNG keep their standard shape. Set `ARMA3_ICON_DIR` or `--icon-dir` to use a different extracted icon folder. Set `show_waypoints=true` to draw active group routes as dashed, side-colored lines with numbered waypoint locations. Route display is off by default. Terrain roads, vegetation, water and contour lines come from the SVG. `max_size` sets the longest image edge (128-2048 px); `labels=true` adds bold, high-contrast names for live overlays and waypoint types, and `include_dead=true` includes dead infantry. By default it searches `svg/tile-svg/<worldName>.svg` relative to the checkout; set `ARMA3_MAP_SVG_DIR` or `--map-svg-dir` to use another folder. The SVG filenames should match the Arma world name (case-insensitive). Export files are not bundled with the package: provide them from `diag_exportTerrainSvg`. No PAA extraction or network map service is needed for the terrain background.

### `execute_sqf`

```json
{"code": "allPlayers apply { [name _x, getPosATL _x] }"}
→ {"ok": true, "type": "ARRAY", "time_ms": 0.08, "result": [["Sebastian", [1834.2, 5622.9, 0.0014]]]}
```

- The value of the last expression is the result, serialized with `toJSON`. Arrays, hashmaps (→ JSON objects), strings, numbers, booleans and nil map directly. Objects, groups, sides and other engine types become their `str` form (`"B Alpha 1-1:1 (Sebastian)"`), non-finite numbers become strings, and hashmap keys that are not strings are `str`'ed. Return a hashmap for structured results.
- `args` is any JSON value and arrives as `_this`, so data does not have to be formatted into code: `{"code": "params ['_cls']; count (allUnits select { typeOf _x == _cls })", "args": ["B_Soldier_F"]}`.
- Code runs **unscheduled** by default: atomically, in one frame, without `sleep` or `waitUntil`. `"scheduled": true` spawns it instead, and the answer comes when the script finishes (raise `timeout` for long ones).
- `throw` is caught and returned as `{"ok": false, "error": ...}`. Code that does not compile returns an error too. Other script errors only reach the RPT log; when the log is on the same machine as the MCP server, the new error lines come back as `rpt_errors`.
- `//` and `/* */` comments are removed before compiling; `#define` and `#include` are not available.
- `"target"` runs the code on another machine in multiplayer: `"server"`, a client's owner id, or a player's name or UID. The code is sent there as code (with `remoteExecCall`), so the client needs nothing installed; a mission whose `CfgRemoteExec` is in whitelist mode has to allow `call` to clients and `a3mcp_fnc_remoteResult` to the server.
- An `ok` result only confirms the SQF request completed; it does not prove the intended in-game effect occurred. Verify important state changes with the relevant inspection tools.
- `list_groups` reports each waypoint's index, type, position, and completion radius. Inspect existing waypoints before replacing or extending a group's route so stale waypoints do not redirect it.
- `create_marker` takes `direction` as a heading in degrees. Use `list_markers` to confirm the stored `dir` for directional markers; the marker text does not set its orientation.
- For helicopter insertion, pair a transport group's `TR UNLOAD` waypoint with a passenger group's `UNLOAD` waypoint at the same landing zone, synchronize the waypoints, and place the passenger group's post-unload route after `UNLOAD`. Confirm passengers have actually left the aircraft and reached the intended landing area before reporting the insertion complete.

## Building

The same way as the AAR recorder: C++17 with CMake, no dependencies.

```sh
cmake -S . -B out -A x64              # Windows; on Linux: cmake -S . -B out -DCMAKE_BUILD_TYPE=Release
cmake --build out --config Release
python tools/pack_mod.py out build/Release/arma3_mcp_x64.dll [build/Release/arma3_mcp_x64.so]
```

`hemtt check` lints the SQF and config. `python -m unittest discover -s tests` loads the built extension into a simulated game (it plays the addon's part through the real callExtension entry points, with the engine's 10 KB output buffer and its argument quoting) and runs the MCP server's client against it: round trips, multi-megabyte UTF-8 requests and results, concurrent and timed-out requests, tokens, and mission restarts. Set `A3MCP_LIB` to test a library somewhere else.

The GitHub Actions workflow in `.github/workflows/build.yml` builds the Windows DLL and the Linux `.so` (on Ubuntu 20.04, so it loads on glibc 2.31+), runs the tests against both, and uploads the `@arma3_mcp` mod folder.

## How it works

The extension listens on TCP and speaks a line protocol (`src/bridge.hpp`), so any program can drive the game, not only this MCP server:

```
AUTH <token>                    -> OK auth            (first, if a token is set)
EXEC <id> {"code": ..., "args": ..., "scheduled": false, "target": "local"}
                                -> RESULT <id> {"ok": true, "type": ..., "result": ...}
                                   or ERROR <id> <reason>
STATUS <id>                     -> STATUS <id> {...}
CANCEL <id>                     (drops a request the game has not started yet)
PING <id>                       -> PONG <id>
```

In the game, a request triggers the `ExtensionCallback` mission event, and `a3mcp_fnc_drain` pulls it with `callExtension ["poll"]` and `["read", [id]]` (pieces sized to the output buffer, never splitting a UTF-8 character). `a3mcp_fnc_execute` compiles and runs it, and `a3mcp_fnc_reply` sends the JSON back in 8000-character pieces with `["result", [id, piece]]` and `["done", [id]]`. A loop also polls every half second in case a callback is dropped, which keeps `game_status` able to tell whether a mission is running.

## Troubleshooting

- **`Cannot reach the Arma 3 extension`**: the game is not running with the mod, or no mission has started yet, or the port is taken. Look for `[arma3_mcp]` in the RPT.
- **`callExtension error ...` / `could not be loaded` in the RPT**: BattlEye blocked it (turn it off for local play), or on Linux the `.so` is not next to `addons/` (`ldd arma3_mcp_x64.so` shows what it misses).
- **`No answer from the game ... not polling`**: you are in the main menu or the editor without previewing; start the mission.
- **Timeouts with `scheduled: true`**: the script is still running; raise `timeout`.
