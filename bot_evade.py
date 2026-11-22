"""ZeroByte evasion experiment: rotate with the zone, break line of sight, never take a fight.

This variant tests whether placement points come mostly from outlasting house bots.
"""
from __future__ import annotations

import math

import mechfight as mf
from bot import Bot, dist2, dist3, unit


class EvadeBot(Bot):
    name = "zero-byte-evade"

    def act(self, obs: mf.Observation) -> mf.Action:
        self.obs = obs
        self.self_obs = me = obs["self"]
        t = obs["t"]
        pos = me["pos"]
        act: mf.Action = {"tick": obs["tick"]}
        if obs.get("offer"):
            act["pick"] = self._pick_upgrade(obs["offer"])

        enemies = [m for m in obs["mechs"] if m["kind"] == "mech"]
        enemies.sort(key=lambda m: dist3(pos, m["pos"]))
        e = enemies[0] if enemies else None
        ed = dist3(pos, e["pos"]) if e else 1e9

        _, _, nxt_c, nxt_r = self._effective_zone(obs)
        d_next = dist2(pos, nxt_c)
        outside = not me["in_zone"]

        # ---- destination: stay inside the next circle, on our side of it ----
        if outside:
            goal = obs["zone"]["center"]
            mode = "reenter"
        elif d_next > nxt_r * 0.40:
            dx, dy = unit(pos[0] - nxt_c[0], pos[1] - nxt_c[1])
            goal = (nxt_c[0] + dx * nxt_r * 0.35, nxt_c[1] + dy * nxt_r * 0.35)
            mode = "rotate"
        else:
            ang = math.atan2(pos[1] - nxt_c[1], pos[0] - nxt_c[0]) + 0.18
            rr = max(12.0, nxt_r * 0.55)
            goal = (nxt_c[0] + math.cos(ang) * rr, nxt_c[1] + math.sin(ang) * rr)
            mode = "hold"

        self._update_path(goal, t)
        wp = self._next_waypoint(goal)
        move = self._move_toward(wp, 1.0) if dist2(pos, wp) > 5.0 else [0.0, 0.0]
        boost = me["energy"] > 45.0 and (outside or d_next > 140.0)

        # ---- avoid the hunter: blend away, and break line of sight when it charges ----
        if e and ed < 190.0:
            away = unit(pos[0] - e["pos"][0], pos[1] - e["pos"][1])
            w = 1.4 if ed < 85.0 else 0.8 if ed < 140.0 else 0.4
            move = [move[0] + away[0] * w, move[1] + away[1] * w]
            if (e.get("charging") and ed < 230.0) or ed < 70.0:
                if t - self.cover_t > 1.2 or self.cover is None:
                    self._find_cover(e["pos"], t)
                if self.cover and dist2(self.cover, nxt_c) < r_safe(nxt_r):
                    move = self._move_toward(self.cover, 1.0)
                    boost = me["energy"] > 20.0
                else:
                    px, py = -away[1], away[0]
                    move = [away[0] * 0.7 + px * 0.7, away[1] * 0.7 + py * 0.7]
                    boost = me["energy"] > 30.0
            mode = "evade"

        if outside:
            move = self._move_toward(obs["zone"]["center"], 1.0)
            boost = me["energy"] > 10.0
            mode = "reenter"

        # ---- stuck handling ----
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

        # ---- look at whoever is hunting us, otherwise look where we are going ----
        if e:
            scale = 1.0 + 0.05 * (e.get("cubes", 0) or 0)
            aim = self._aim_at([e["pos"][0], e["pos"][1], e["pos"][2] + 4.5 * scale])
        elif ml > 0.2:
            self.scan += 0.08
            aim = (math.atan2(move[1], move[0]) + 0.45 * math.sin(self.scan), -0.05)
        else:
            aim = self.self_obs["aim"]

        act.update(move=[float(move[0]), float(move[1])], boost=bool(boost), jump=bool(jump),
                   aim=[float(aim[0]), float(aim[1])], fire=[False, False])
        mf.draw.label(f"{mode} hp{me['hp']:.0f}" + (f" e{ed:.0f}" if e else ""))
        return act


def r_safe(nxt_r):
    return max(25.0, nxt_r * 0.95)


if __name__ == "__main__":
    mf.run(EvadeBot)
