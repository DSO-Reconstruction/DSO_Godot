#!/usr/bin/env python3
"""Export the static game database as a runtime-ready JSON bundle.

The client's `static.db4` holds 197 tables; a runtime needs a fraction of it.
This tool emits only what the simulation reads, with the modifier language
already parsed into structured form, so the runtime never parses text.

By default it keeps just the effects reachable from the playable classes'
skills, following the references transitively. Most of the 6,703 status
effects belong to monsters, items or events.

    python3 export_gamedata.py --db path/to/static.db4 --out gamedata
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dsoexp import gamedb

PLAYABLE = ("warrior", "mage", "ranger", "dwarf", "niwalk")

# Skill columns worth carrying into the runtime.
SKILL_FIELDS = {
    "Name": "name", "CharClass": "char_class", "SkillType": "skill_type",
    "SkillCategory": "category", "TargetingType": "targeting",
    "CoolDown": "cooldown", "InitialCoolDown": "initial_cooldown",
    "CoolDownCategory": "cooldown_category", "ResourceCost": "resource_cost",
    "ResourceGain": "resource_gain", "AmmoCost": "ammo_cost",
    "AttackRange": "range", "HitRange": "hit_range", "Angle": "angle",
    "PlayerWidth": "width", "EndWidth": "end_width",
    "DamageType": "damage_type", "DamageModifier": "damage_modifier",
    "UseWeaponDPS": "use_weapon_dps", "UnlockLevel": "unlock_level",
    "HitFrame": "hit_frame", "SkillUnblockFrame": "unblock_frame",
    "MotionUnblockFrame": "motion_unblock_frame",
    "InvincibilityFrames": "invincibility_frames",
    "LoopStartFrame": "loop_start_frame", "BulletCount": "bullet_count",
    "BulletEmitDelay": "bullet_emit_delay", "SkillBulletId": "bullet_id",
    "ReproductionCount": "reproduction_count", "HeavyHitChance": "heavy_hit_chance",
    "ExecuteSequenceMapping": "seq_execute",
    "PreExecuteSequenceMapping": "seq_pre_execute",
    "PostExecuteSequenceMapping": "seq_post_execute",
    "SkillImpactSequence": "seq_impact",
    "Summons": "summons", "SummonMonsterId": "summon_monster",
    "IsLive": "is_live", "Hidden": "hidden",
}

EFFECT_FIELDS = {
    "StatusEffectDuration": "duration",
    "StatusEffectDurationPvP": "duration_pvp",
    "StatusEffectTickRate": "tick_rate",
    "StatusEffectRealTime": "real_time",
    "MaxStackSize": "max_stack", "ExclusiveGroup": "exclusive_group",
    "ExclusivityType": "exclusivity", "OpposingEffectId": "opposing_effect",
    "Groups": "groups", "Persistent": "persistent",
    "KeepOnDeath": "keep_on_death", "CharClass": "char_class",
    "AuraShape": "aura_shape", "AuraRadius": "aura_radius",
    "AuraLength": "aura_length", "AuraWidth": "aura_width",
    "AuraHeight": "aura_height", "AuraEffectId": "aura_effect",
    "AuraTargets": "aura_targets", "ShowCastbar": "show_castbar",
}

PHASES = {"StartModifiers": "start", "TickModifiers": "tick",
          "StopModifiers": "stop", "DoneModifiers": "done",
          "OnResumeStartModifiers": "resume"}

EFFECT_LISTS = {"UserStatusEffects": "user_effects",
                "VictimStatusEffects": "victim_effects",
                "LocationStatusEffects": "location_effects"}


def val(v):
    """Serialise a parsed Value compactly: n=number, p=param, i=identifier."""
    out = {}
    if v.number is not None:
        out["n"] = v.number
    elif v.param is not None:
        out["p"] = v.param
    elif v.ident is not None:
        out["i"] = v.ident
    if v.mode:
        out["mode"] = v.mode
    if v.tag and v.tag != gamedb.NO_TAG:
        out["tag"] = v.tag
    if v.qualifier:
        out["q"] = v.qualifier
    return out


def mod(m):
    d = {"verb": m.verb}
    if m.args:
        d["args"] = [val(a) for a in m.args]
    if m.named:
        d["named"] = {k: val(v) for k, v in m.named.items()}
    return d


def ref(r):
    d = {"id": r.effect_id}
    if r.chance != 1.0:
        d["chance"] = r.chance
    if r.duration is not None:
        d["duration"] = r.duration
    if r.duration_pvp is not None:
        d["duration_pvp"] = r.duration_pvp
    if r.trigger:
        d["trigger"] = r.trigger
    if r.params:
        d["params"] = {k: val(v) for k, v in r.params.items()}
    return d


def clean(value):
    """Drop empty strings and nulls so the bundle stays small."""
    return value not in (None, "", "~")


def effect_ids_in_modifiers(mods):
    """Status-effect ids a modifier list references.

    Effect-control verbs name another effect in their arguments, so the set of
    reachable effects has to be closed transitively.
    """
    out = set()
    for m in mods:
        if m.verb in ("ActorStatusEffect", "SkillStatusEffect"):
            # 'action, [skill,] effect_id, ...' -- take every identifier and
            # let the caller intersect with the known effect ids.
            for a in m.args:
                if a.ident:
                    out.add(a.ident)
        elif m.verb in ("StopStatusEffect", "StatusEffectImmunityGroup",
                        "StopStatusEffectGroup"):
            for a in m.args:
                if a.ident:
                    out.add(a.ident)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, help="path to static.db4")
    ap.add_argument("--out", default="gamedata", help="output directory")
    ap.add_argument("--classes", nargs="*", default=list(PLAYABLE))
    ap.add_argument("--all-effects", action="store_true",
                    help="export every status effect, not just the reachable ones")
    ap.add_argument("--indent", type=int, default=None,
                    help="pretty-print the JSON (bigger files)")
    a = ap.parse_args()

    db = gamedb.connect(a.db)
    attrs = gamedb.load_attributes(db)
    all_effects = gamedb.load_status_effects(db)
    all_skills = gamedb.load_skills(db)
    print(f"[db] {len(attrs)} attributes, {len(all_skills)} skills, "
          f"{len(all_effects)} status effects", flush=True)

    wanted = set(a.classes)
    skills = {}
    for sid, r in all_skills.items():
        if (r.get("CharClass") or "") not in wanted:
            continue
        s = {out: r[col] for col, out in SKILL_FIELDS.items() if clean(r.get(col))}
        for col, out in EFFECT_LISTS.items():
            refs = gamedb.parse_effect_list(r.get(col))
            if refs:
                s[out] = [ref(x) for x in refs]
        skills[sid] = s
    print(f"[skills] {len(skills)} for classes {sorted(wanted)}", flush=True)

    # Transitive closure over the effects those skills can apply.
    if a.all_effects:
        reachable = set(all_effects)
    else:
        frontier = set()
        for s in skills.values():
            for key in EFFECT_LISTS.values():
                for r in s.get(key, []):
                    frontier.add(r["id"])
        reachable, depth = set(), 0
        while frontier:
            frontier &= set(all_effects)
            frontier -= reachable
            if not frontier:
                break
            reachable |= frontier
            depth += 1
            nxt = set()
            for eid in frontier:
                for mods in all_effects[eid]["_modifiers"].values():
                    nxt |= effect_ids_in_modifiers(mods)
            frontier = nxt
        print(f"[effects] {len(reachable)} reachable, closure depth {depth}",
              flush=True)

    effects = {}
    for eid in sorted(reachable):
        r = all_effects[eid]
        e = {out: r[col] for col, out in EFFECT_FIELDS.items() if clean(r.get(col))}
        phases = {}
        for col, name in PHASES.items():
            mods = r["_modifiers"].get(col)
            if mods:
                phases[name] = [mod(m) for m in mods]
        if phases:
            e["phases"] = phases
        effects[eid] = e

    # Only the attributes the exported modifiers actually touch, plus the
    # registry itself for reference.
    touched = set()
    for e in effects.values():
        for mods in e.get("phases", {}).values():
            for m in mods:
                touched.add(m["verb"])

    os.makedirs(a.out, exist_ok=True)
    bundle = {
        "version": 1,
        "source": os.path.basename(a.db),
        "attributes": attrs,
        "skills": skills,
        "effects": effects,
    }
    path = os.path.join(a.out, "gamedata.json")
    with open(path, "w") as f:
        json.dump(bundle, f, separators=(",", ":"), indent=a.indent)

    verbs = {}
    for e in effects.values():
        for mods in e.get("phases", {}).values():
            for m in mods:
                verbs[m["verb"]] = verbs.get(m["verb"], 0) + 1
    with open(os.path.join(a.out, "verbs.json"), "w") as f:
        json.dump(dict(sorted(verbs.items(), key=lambda x: -x[1])), f, indent=1)

    print(f"[out] {path} -- {os.path.getsize(path)/1048576:.1f} MB")
    print(f"[out] {len(verbs)} distinct verbs in the exported effects")
    for v, n in sorted(verbs.items(), key=lambda x: -x[1])[:10]:
        print(f"       {n:5}  {v}")


if __name__ == "__main__":
    main()
