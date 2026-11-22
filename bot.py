"""ZeroByte — Mech Royale controller.

Strategy: rotate with the zone, loot weapons/cubes, and fight house bots at
range with cover and strafing. Placement is what scores, so disengage when low
and let the zone do work.
"""
from __future__ import annotations

import array
import heapq
import json
import math
import os

import mechfight as mf

GRID = 4.0            # navigation grid spacing, meters
INFLATE = 4.0         # obstacle inflation, meters
MECH_H = 9.0
STEP_UP = 1.2
SQRT2 = math.sqrt(2.0)

# Upgrade preference, best first.
UPGRADE_PRIORITY = [
    "plating", "armor", "lightning_efficiency", "lightning_chain",
    "lightning_range", "gun_damage", "gun_rate", "capacitor", "reactor",
    "nanites", "laser_damage", "laser_charge", "gun_mag", "laser_pierce",
    "servos", "gyros", "sensors", "gun_velocity", "mortar_reload",
    "mortar_splash", "mortar_velocity", "jets",
]


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def dist2(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def dist3(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def unit(x, y):
    n = math.hypot(x, y)
    if n < 1e-9:
        return 0.0, 0.0
    return x / n, y / n


def lerp(a, b, f):
    return a + (b - a) * f


def point_in_poly(x, y, ring):
    inside = False
    n = len(ring) // 2
    j = n - 1
    for i in range(n):
        xi, yi = ring[2 * i], ring[2 * i + 1]
        xj, yj = ring[2 * j], ring[2 * j + 1]
        if (yi > y) != (yj > y):
            xint = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < xint:
                inside = not inside
        j = i
    return inside


class WorldMap:
    """Static arena: terrain heights + blocked cells for pathing."""

    def __init__(self, map_dir):
        self.ok = False
        self.minx = self.miny = 0.0
        self.gw = self.gh = 0
        self.heights = []
        self.walk = bytearray()
        self.terrain = None
        try:
            # The conformance checker rewrites map.dir with the host's path rules;
            # inside the match container the map is always at /arena.
            candidates = [map_dir]
            if os.path.abspath(map_dir) != "/arena":
                candidates.append("/arena")
            for cand in candidates:
                if os.path.exists(os.path.join(cand, "v2", "world.json")):
                    map_dir = cand
                    break
            with open(os.path.join(map_dir, "v2", "world.json")) as f:
                w = json.load(f)
            t = w["terrain"]
            self.minx, self.miny = t["minX"], t["minY"]
            self.step = t["step"]
            self.nx, self.ny = t["nx"], t["ny"]
            raw = open(os.path.join(map_dir, "v2", "terrain.i16"), "rb").read()
            a = array.array("h")
            a.frombytes(raw[: self.nx * self.ny * 2])
            self.terrain = a
            b = w["bounds"]
            self.gw = int(math.ceil((b["maxX"] - self.minx) / GRID)) + 1
            self.gh = int(math.ceil((b["maxY"] - self.miny) / GRID)) + 1
            n = self.gw * self.gh
            self.heights = [0.0] * n
            for j in range(self.gh):
                y = self.miny + j * GRID
                for i in range(self.gw):
                    self.heights[j * self.gw + i] = self.ground(self.minx + i * GRID, y)
            blocked = bytearray(n)
            for p in w.get("prisms", []):
                ring = p.get("ring") or []
                if len(ring) < 6:
                    continue
                xs, ys = ring[0::2], ring[1::2]
                i0 = max(0, int((min(xs) - self.minx) / GRID))
                i1 = min(self.gw - 1, int((max(xs) - self.minx) / GRID))
                j0 = max(0, int((min(ys) - self.miny) / GRID))
                j1 = min(self.gh - 1, int((max(ys) - self.miny) / GRID))
                holes = [h for h in p.get("holes") or [] if len(h) >= 6]
                base, top = p.get("base", -100.0), p.get("top", 100.0)
                for j in range(j0, j1 + 1):
                    y = self.miny + j * GRID
                    for i in range(i0, i1 + 1):
                        x = self.minx + i * GRID
                        if not point_in_poly(x, y, ring):
                            continue
                        if any(point_in_poly(x, y, h) for h in holes):
                            continue
                        k = j * self.gw + i
                        g = self.heights[k]
                        if base < g + MECH_H and top > g + STEP_UP:
                            blocked[k] = 1
            # inflate obstacles by one cell (4 m)
            infl = bytearray(blocked)
            for j in range(self.gh):
                for i in range(self.gw):
                    k = j * self.gw + i
                    if not blocked[k]:
                        continue
                    for dj in (-1, 0, 1):
                        jj = j + dj
                        if jj < 0 or jj >= self.gh:
                            continue
                        for di in (-1, 0, 1):
                            ii = i + di
                            if 0 <= ii < self.gw:
                                infl[jj * self.gw + ii] = 1
            self.walk = bytes(1 - v for v in infl)
            self.ok = True
        except Exception as e:  # never let map trouble kill the bot
            mf.log("map load failed:", repr(e))

    def ground(self, x, y):
        if self.terrain is None:
            return 0.0
        fx = (x - self.minx) / self.step
        fy = (y - self.miny) / self.step
        i0 = int(math.floor(fx))
        j0 = int(math.floor(fy))
        i0 = clamp(i0, 0, self.nx - 2)
        j0 = clamp(j0, 0, self.ny - 2)
        tx = clamp(fx - i0, 0.0, 1.0)
        ty = clamp(fy - j0, 0.0, 1.0)
        a = self.terrain
        n = self.nx
        h00 = a[j0 * n + i0]
        h10 = a[j0 * n + i0 + 1]
        h01 = a[(j0 + 1) * n + i0]
        h11 = a[(j0 + 1) * n + i0 + 1]
        return ((h00 * (1 - tx) + h10 * tx) * (1 - ty)
                + (h01 * (1 - tx) + h11 * tx) * ty) * 0.01

    def cell(self, x, y):
        i = int(round((x - self.minx) / GRID))
        j = int(round((y - self.miny) / GRID))
        return clamp(i, 0, self.gw - 1), clamp(j, 0, self.gh - 1)

    def center(self, i, j):
        return self.minx + i * GRID, self.miny + j * GRID

    def walkable(self, x, y):
        if not self.ok:
            return True
        i, j = self.cell(x, y)
        return bool(self.walk[j * self.gw + i])

    def nearest_walkable(self, i, j, radius=8):
        if self.walk[j * self.gw + i]:
            return i, j
        best = None
        for r in range(1, radius + 1):
            for dj in range(-r, r + 1):
                for di in range(-r, r + 1):
                    if max(abs(di), abs(dj)) != r:
                        continue
                    ii, jj = i + di, j + dj
                    if 0 <= ii < self.gw and 0 <= jj < self.gh and self.walk[jj * self.gw + ii]:
                        d = di * di + dj * dj
                        if best is None or d < best[0]:
                            best = (d, ii, jj)
            if best:
                return best[1], best[2]
        return None

    def astar(self, start, goal, max_expand=9000):
        if not self.ok:
            return []
        si, sj = self.cell(start[0], start[1])
        gi, gj = self.cell(goal[0], goal[1])
        nw = self.nearest_walkable(si, sj)
        ng = self.nearest_walkable(gi, gj)
        if nw is None or ng is None:
            return []
        si, sj = nw
        gi, gj = ng
        gw, gh, walk = self.gw, self.gh, self.walk
        startk = sj * gw + si
        goalk = gj * gw + gi
        if startk == goalk:
            return [self.center(gi, gj)]
        gscore = {startk: 0.0}
        came = {}
        h0 = math.hypot(si - gi, sj - gj)
        heap = [(h0, 0.0, startk)]
        expanded = 0
        while heap:
            f, g, k = heapq.heappop(heap)
            if k == goalk:
                path = []
                while k in came:
                    j, i = divmod(k, gw)
                    path.append(self.center(i, j))
                    k = came[k]
                path.reverse()
                return path
            if g > gscore.get(k, 1e18):
                continue
            expanded += 1
            if expanded > max_expand:
                break
            j, i = divmod(k, gw)
            for dj in (-1, 0, 1):
                for di in (-1, 0, 1):
                    if di == 0 and dj == 0:
                        continue
                    ii, jj = i + di, j + dj
                    if ii < 0 or jj < 0 or ii >= gw or jj >= gh:
                        continue
                    kk = jj * gw + ii
                    if not walk[kk]:
                        continue
                    if di and dj and (not walk[j * gw + ii] or not walk[jj * gw + i]):
                        continue
                    ng2 = g + (SQRT2 if di and dj else 1.0)
                    if ng2 < gscore.get(kk, 1e18):
                        gscore[kk] = ng2
                        came[kk] = k
                        h = math.hypot(ii - gi, jj - gj)
                        heapq.heappush(heap, (ng2 + h, ng2, kk))
        return []

    def line_clear(self, a, b, samples=14):
        """Approximate 2-D obstruction check on the nav grid."""
        if not self.ok:
            return True
        for s in range(1, samples):
            f = s / samples
            x = a[0] + (b[0] - a[0]) * f
            y = a[1] + (b[1] - a[1]) * f
            if not self.walkable(x, y):
                return False
        return True


class Bot:
    name = "zero-byte"

    def __init__(self, hello: mf.Hello):
        self.c = hello["config"]
        self.mech = self.c["mech"]
        self.weapons_cfg = self.c["weapons"]
        self.vision = self.c["vision"]
        self.loot_cfg = self.c["loot"]
        self.zone_cfg = self.c["zone"]
        self.world = WorldMap(hello["map"]["dir"])
        self.path = []
        self.path_goal = None
        self.path_t = -99.0
        self.path_i = 0
        self.scan = 0.0
        self.strafe = 1.0
        self.strafe_t = 0.0
        self.last_pos = None
        self.stuck_t = 0.0
        self.jump_t = -9.0
        self.retreat = False
        self.laser_hold = False
        self.cover = None
        self.cover_t = -9.0
        self.npc_target = None
        self.upgrade_ids = {u["id"]: u for u in self.c.get("upgrades", [])}

    # ---------- helpers ----------
    def _effective_zone(self, obs):
        z = obs["zone"]
        t = obs["t"]
        if t <= z["shrink_start"]:
            return z["center"], z["radius"], z["next_center"], z["next_radius"]
        if t < z["shrink_end"]:
            span = max(0.001, z["shrink_end"] - z["shrink_start"])
            f = (t - z["shrink_start"]) / span
            c = (lerp(z["center"][0], z["next_center"][0], f),
                 lerp(z["center"][1], z["next_center"][1], f))
            r = lerp(z["radius"], z["next_radius"], f)
            return list(c), r, z["next_center"], z["next_radius"]
        return z["next_center"], z["next_radius"], z["next_center"], z["next_radius"]

    def _aim_at(self, target, lead=0.0, target_vel=None):
        me = self.self_obs
        x, y, z = me["pos"]
        mz = z + me["stats"].get("muzzle_height", 7.0)
        tx, ty, tz = target
        if target_vel and lead:
            tx += target_vel[0] * lead
            ty += target_vel[1] * lead
            tz += target_vel[2] * lead
        yaw = math.atan2(ty - y, tx - x)
        hd = math.hypot(tx - x, ty - y)
        pitch = math.atan2(tz - mz, max(0.1, hd))
        pitch = clamp(pitch, self.mech["min_pitch"], self.mech["max_pitch"])
        return yaw, pitch

    def _aim_error(self, yaw, pitch):
        ay, ap = self.self_obs["aim"]
        dy = (yaw - ay + math.pi) % (2 * math.pi) - math.pi
        return abs(dy), abs(pitch - ap)

    def _gun_intercept(self, m):
        me = self.self_obs
        speed = self.weapons_cfg["gun"]["speed"]
        if me["weapons"][0] and me["weapons"][0]["kind"] == "gun":
            speed = me["weapons"][0]["stats"].get("speed", speed)
        tp = m["pos"]
        tv = m.get("vel") or [0, 0, 0]
        d = dist3(me["pos"], tp)
        t = d / max(1.0, speed)
        for _ in range(3):
            px = tp[0] + tv[0] * t
            py = tp[1] + tv[1] * t
            pz = tp[2] + tv[2] * t
            t = math.sqrt((px - me["pos"][0]) ** 2 + (py - me["pos"][1]) ** 2
                          + (pz - me["pos"][2]) ** 2) / max(1.0, speed)
        return [tp[0] + tv[0] * t, tp[1] + tv[1] * t, tp[2] + tv[2] * t], t

    def _pick_upgrade(self, offer):
        best, bi = 1e9, 0
        for i, ch in enumerate(offer.get("choices", [])):
            pr = UPGRADE_PRIORITY
            try:
                rank = pr.index(ch["id"])
            except ValueError:
                rank = 50
            if rank < best:
                best, bi = rank, i
        return bi

    def _move_toward(self, target, speed=1.0):
        me = self.self_obs
        dx, dy = unit(target[0] - me["pos"][0], target[1] - me["pos"][1])
        return [dx * speed, dy * speed]

    def _update_path(self, goal, t):
        if not self.world.ok:
            return
        moved = (self.path_goal is None or dist2(goal, self.path_goal) > 9.0
                 or t - self.path_t > 2.5 or (self.path and self.path_i >= len(self.path)))
        if moved:
            p = self.world.astar(self.self_obs["pos"], goal)
            if p:
                self.path = p
                self.path_i = 0
                self.path_goal = list(goal)
                self.path_t = t
            else:
                self.path = []
                self.path_goal = list(goal)
                self.path_t = t
        while self.path_i < len(self.path) and dist2(self.self_obs["pos"], self.path[self.path_i]) < 7.0:
            self.path_i += 1

    def _next_waypoint(self, fallback):
        if self.path_i < len(self.path):
            return self.path[self.path_i]
        return fallback

    def _find_cover(self, threat, t):
        if not self.world.ok:
            return None
        me = self.self_obs["pos"]
        best = None
        for ang in range(0, 360, 30):
            a = math.radians(ang)
            p = (me[0] + math.cos(a) * 38.0, me[1] + math.sin(a) * 38.0)
            if not self.world.walkable(*p):
                continue
            if not self.world.line_clear(p, threat):
                d = dist2(me, p)
                if best is None or d < best[0]:
                    best = (d, p)
        self.cover = best[1] if best else None
        self.cover_t = t
        return self.cover

    def _ground_reach(self, p):
        """True if loot is close enough to the walkable ground to collect."""
        if not self.world.ok:
            return True
        return p[2] - self.world.ground(p[0], p[1]) <= 2.6

    # ---------- main ----------
    def act(self, obs: mf.Observation) -> mf.Action:
        self.obs = obs
        self.self_obs = me = obs["self"]
        t = obs["t"]
        pos = me["pos"]
        max_hp = me["max_hp"]
        act: mf.Action = {"tick": obs["tick"]}
        if obs.get("offer"):
            act["pick"] = self._pick_upgrade(obs["offer"])

        # ----- threat assessment -----
        enemies = [m for m in obs["mechs"] if m["kind"] == "mech"]
        npcs = [m for m in obs["mechs"] if m["kind"] == "npc"]

        def threat_key(m):
            d = dist3(pos, m["pos"])
            hp = m.get("hp", 1400.0)
            return d - min(90.0, max(0.0, 1400.0 - hp) * 0.11)

        enemies.sort(key=threat_key)
        target = enemies[0] if enemies else None
        td = dist3(pos, target["pos"]) if target else 1e9
        low = me["hp"] < 0.42 * max_hp

        zone_c, zone_r, nxt_c, nxt_r = self._effective_zone(obs)
        d_zone = dist2(pos, nxt_c)
        outside = not me["in_zone"]
        zone_urgent = outside or d_zone > nxt_r - max(8.0, nxt_r * 0.25) or (obs["zone"]["shrink_start"] - t < 12.0 and d_zone > nxt_r * 0.7)
        rotate_now = outside or d_zone > nxt_r + 10.0

        # ----- mode / objective -----
        mode = "zone"
        goal = nxt_c
        if target and td < 170.0:
            mode = "fight"
            if low and td > 55.0 and target.get("hp", 1400.0) > me["hp"] * 0.8:
                mode = "retreat"
        elif npcs and me["hp"] > 0.9 * max_hp and t > 15.0:
            n = min(npcs, key=lambda m: dist3(pos, m["pos"]))
            if dist3(pos, n["pos"]) < 70.0 and not any(
                    dist3(pos, p["pos"]) < 120.0 for p in obs["pickups"]):
                mode = "npc"
                goal = n["pos"]
                self.npc_target = n

        # weapon loot: the laser is the ranged duel weapon; grab one early
        if me["weapons"][1] is None and obs["pickups"] and not outside:
            pref = {"laser": 0, "lightning": 1, "mortar": 2}
            cand = [p for p in obs["pickups"] if p["kind"] in pref and self._ground_reach(p["pos"])]
            if cand:
                cand.sort(key=lambda p: (pref[p["kind"]], dist3(pos, p["pos"])))
                p = cand[0]
                if dist3(pos, p["pos"]) < 190.0 and (target is None or td > 55.0 or t < 20.0):
                    goal = p["pos"]
                    mode = "loot"

        # crates early
        if mode == "zone" and obs["crates"] and me["hp"] > 0.5 * max_hp:
            cs = sorted((c for c in obs["crates"] if self._ground_reach(c["pos"])),
                        key=lambda c: dist3(pos, c["pos"]))
            if cs:
                c = cs[0]
                if dist3(pos, c["pos"]) < 45.0:
                    mode = "crate"
                    goal = c["pos"]

        # cubes within reach
        if mode in ("zone", "loot") and obs["cubes"]:
            cb = sorted((c for c in obs["cubes"] if self._ground_reach(c["pos"])),
                        key=lambda c: dist3(pos, c["pos"]))
            if cb:
                c = cb[0]
                if dist3(pos, c["pos"]) < 22.0:
                    goal = c["pos"]
                    mode = "cube"

        # supply drop when safe
        drops_now = [d for d in (obs.get("drops") or [])
                     if d.get("lands_in", 1.0) <= 0.0 and d.get("holder") is None]
        if drops_now and mode in ("zone", "loot", "cube", "crate") and (target is None or td > 170.0):
            d = min(drops_now, key=lambda d: dist2(pos, d["pos"]))
            if dist2(pos, d["pos"]) < 140.0 and not any(dist3(pos, e["pos"]) < 55.0 for e in enemies):
                mode = "drop"
                goal = d["pos"]

        # zone outranks looting when the next circle is far
        if rotate_now and mode in ("loot", "crate", "cube", "npc"):
            mode = "zone"
            goal = nxt_c

        # ----- movement -----
        speed = 1.0
        boost = False
        move = [0.0, 0.0]
        if mode == "fight":
            dx, dy = unit(target["pos"][0] - pos[0], target["pos"][1] - pos[1])
            if t - self.strafe_t > 1.4:
                self.strafe *= -1.0
                self.strafe_t = t
            px, py = -dy * self.strafe, dx * self.strafe
            myk = set(w["kind"] for w in me["weapons"] if w)
            ek = set(target.get("weapons") or [])
            if "laser" in myk:
                lo, hi = (75.0, 160.0)
            elif "lightning" in myk:
                lo, hi = (60.0, 120.0)
            else:
                lo, hi = (45.0, 90.0)
            t_hp = target.get("hp", 1400.0)
            if t_hp < 320.0 and td > 28.0:
                # finish the kill
                move = [dx * 0.9 + px * 0.15, dy * 0.9 + py * 0.15]
                boost = me["energy"] > 50.0 and "lightning" not in myk
            elif td < lo:
                move = [(-dx * 0.8 + px * 0.6), (-dy * 0.8 + py * 0.6)]
            elif td > hi:
                move = [dx * 0.85 + px * 0.35, dy * 0.85 + py * 0.35]
                boost = me["energy"] > 55.0 and (td > 110.0 or "lightning" in myk)
            else:
                move = [px * 0.9 + dx * 0.1, py * 0.9 + dy * 0.1]
            if target.get("charging") and "lightning" not in myk:
                # a laser is charging: get behind cover if there is any
                if td > 50.0:
                    if t - self.cover_t > 1.0 or self.cover is None:
                        self._find_cover(target["pos"], t)
                    if self.cover and dist2(self.cover, nxt_c) < max(30.0, nxt_r * 0.9):
                        move = self._move_toward(self.cover, 1.0)
                        boost = me["energy"] > 20.0
                    else:
                        move = [px + dx * 0.05, py + dy * 0.05]
                        boost = me["energy"] > 35.0
                else:
                    move = [px + dx * 0.05, py + dy * 0.05]
                    boost = me["energy"] > 35.0
            if outside or d_zone > nxt_r * 0.8:
                zd = unit(nxt_c[0] - pos[0], nxt_c[1] - pos[1])
                move = [move[0] + zd[0] * 0.9, move[1] + zd[1] * 0.9]
                boost = me["energy"] > 30.0
        elif mode == "retreat":
            if t - self.cover_t > 1.5 or self.cover is None:
                self._find_cover(target["pos"], t)
            p = self.cover or (pos[0] - (target["pos"][0] - pos[0]), pos[1] - (target["pos"][1] - pos[1]))
            move = self._move_toward(p, 1.0)
            boost = me["energy"] > 25.0
        else:
            self._update_path(goal, t)
            goalp = self._next_waypoint(goal)
            if mode == "drop" and dist2(pos, goal) < 4.0:
                move = [0.0, 0.0]
                boost = False
            elif dist2(pos, goalp) > 5.0:
                move = self._move_toward(goalp, speed)
            boost = me["energy"] > 55.0 and (outside or d_zone > 130.0) and mode != "drop"

        # zone override: never stand outside
        if outside:
            move = self._move_toward(nxt_c if d_zone < 400 else obs["zone"]["center"], 1.0)
            boost = me["energy"] > 20.0
            goal = nxt_c

        # stuck detection / jump
        if self.last_pos is not None and t > 1.0:
            moved = dist2(pos, self.last_pos)
            if moved < 0.35 and (abs(move[0]) + abs(move[1])) > 0.5:
                self.stuck_t += 0.1
            else:
                self.stuck_t = max(0.0, self.stuck_t - 0.2)
        self.last_pos = list(pos)
        jump = False
        if self.stuck_t > 0.7 and t - self.jump_t > 0.5:
            jump = True
            self.jump_t = t
            boost = True
            self.stuck_t = 0.0
        if jump and not me["grounded"]:
            jump = False

        ml = math.hypot(move[0], move[1])
        if ml > 1.0:
            move = [move[0] / ml, move[1] / ml]

        # ----- aim -----
        if t < 3.0 and me["weapons"][1] is None and not target:
            # spin the torso at spawn to spot floor loot in every direction
            aim = (me["aim"][0] + 0.38, -0.02)
        elif target:
            tpos = target["pos"]
            scale = 1.0 + 0.05 * (target.get("cubes", 0) or 0)
            center = [tpos[0], tpos[1], tpos[2] + 4.5 * scale]
            aim = self._aim_at(center)
            # The server only lists mechs it can see, so trust that line of sight;
            # the nav grid is inflated and would veto too many shots.
            los = True
        elif mode == "crate" and goal:
            aim = self._aim_at([goal[0], goal[1], goal[2] + 1.2])
        elif mode == "npc" and goal:
            aim = self._aim_at([goal[0], goal[1], goal[2] + 1.8])
        else:
            # scan while travelling
            if ml > 0.2:
                base = math.atan2(move[1], move[0])
            else:
                base = math.atan2(nxt_c[1] - pos[1], nxt_c[0] - pos[0])
            self.scan += 0.09
            aim = (base + 0.55 * math.sin(self.scan), -0.05)

        # ----- weapons -----
        fire0 = fire1 = False
        reload = False
        take = None
        drop_slot = None
        w0, w1 = me["weapons"][0], me["weapons"][1]
        if target and los:
            scale = 1.0 + 0.05 * (target.get("cubes", 0) or 0)
            center = [target["pos"][0], target["pos"][1], target["pos"][2] + 4.5 * scale]
            center_aim = self._aim_at(center)
            gaim = None
            if w0 and w0["kind"] == "gun":
                gp, _ = self._gun_intercept(target)
                gaim = self._aim_at(gp)
            primary = "gun"
            if w1 and w1["kind"] == "laser" and td < w1["stats"].get("range", 280.0) * 0.9:
                primary = "laser"
            elif w1 and w1["kind"] == "lightning" and td < w1["stats"].get("range", 45.0) * 0.95:
                primary = "lightning"
            if primary == "laser":
                aim = self._aim_at(center, lead=0.08, target_vel=target.get("vel"))
                ey, ep = self._aim_error(*aim)
                ch = w1.get("charge", 0.0)
                full = w1["stats"].get("charge", 0.75)
                if ch > 0.0:
                    if not (ch >= full * 0.85 and ey < 0.02 and ep < 0.03):
                        fire1 = True
                elif w1.get("cooldown", 0.0) <= 0.01 and ey < 0.03:
                    fire1 = True
                if w0 and gaim and td < 65.0:
                    gy, gpch = self._aim_error(*gaim)
                    if gy < 0.08 and gpch < 0.07:
                        fire0 = True
            elif primary == "lightning":
                aim = self._aim_at(center, lead=0.05, target_vel=target.get("vel"))
                ey, ep = self._aim_error(*aim)
                fire1 = ey < 0.32 and me["energy"] > 10.0
                if w0 and gaim and td < 60.0:
                    gy, gpch = self._aim_error(*gaim)
                    if gy < 0.08 and gpch < 0.07:
                        aim = gaim
                        fire0 = True
            else:
                if w0 and gaim:
                    aim = gaim
                    gy, gpch = self._aim_error(*aim)
                    if td < 75.0 and gy < 0.03 and gpch < 0.03:
                        fire0 = True
        elif mode == "crate" and goal:
            ey, ep = self._aim_error(*aim)
            if ey < 0.06 and ep < 0.05:
                fire0 = True
        elif mode == "npc" and goal and self.npc_target:
            if w0:
                gp, _ = self._gun_intercept(self.npc_target)
                gaim = self._aim_at(gp)
                ey, ep = self._aim_error(*gaim)
                if ey < 0.07 and ep < 0.06:
                    aim = gaim
                    fire0 = True
        if w0 and w0.get("ammo", 1) <= 1 and w0.get("reload", 0) == 0:
            reload = True

        # weapon pickup / swap: laser first at kiting range
        if obs["pickups"]:
            pref = {"laser": 0, "lightning": 1, "mortar": 2}
            cand = [p for p in obs["pickups"] if p["kind"] in pref and self._ground_reach(p["pos"])]
            if w1 is None and cand:
                cand.sort(key=lambda p: (pref[p["kind"]], dist3(pos, p["pos"])))
                p = cand[0]
                if (dist2(pos, p["pos"]) <= self.loot_cfg["pickup_radius"] + 0.4
                        and abs(p["pos"][2] - pos[2]) <= 4.0):
                    take = p["id"]
            elif w1 is not None and w1["kind"] in ("mortar", "lightning"):
                good = [p for p in cand if p["kind"] in ("laser", "lightning")
                        and dist3(pos, p["pos"]) < 14.0]
                if good:
                    good.sort(key=lambda p: (pref[p["kind"]], dist3(pos, p["pos"])))
                    take = good[0]["id"]
                    drop_slot = 1

        act.update(move=move, boost=bool(boost), jump=bool(jump), aim=[float(aim[0]), float(aim[1])],
                   fire=[fire0, fire1])
        if reload:
            act["reload"] = True
        if take is not None:
            act["take"] = take
        if drop_slot is not None:
            act["drop"] = drop_slot

        # debug label
        mf.draw.label(f"{mode} hp{me['hp']:.0f} c{me['cubes']:.0f}"
                      + (f" t{td:.0f}" if target else ""))
        return act


if __name__ == "__main__":
    mf.run(Bot)
