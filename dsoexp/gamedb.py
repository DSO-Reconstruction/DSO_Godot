"""Reader for the client's static game database (`db/static.db4`).

The file is a plain SQLite database holding the game's static data model:
197 tables, of which the ones that matter for gameplay are

    _Attributes             the stat registry (typed, named)
    _Template_Skill         skills: timing, targeting, effects applied
    _Template_StatusEffect  the universal effect engine (buff/debuff/DoT/aura)
    _Template_Item          items
    _Template_Monster       monsters

Skills do not compute anything themselves. They apply status effects, and
status effects carry *modifier* lists that read and write attributes:

    Skill -> StatusEffect -> Modifier -> Attribute

## The modifier language

Modifier columns hold a small text language. Grammar, as observed across all
5,487 non-empty modifier fields:

    modifiers := part (';' part)*
    part      := verb ':' arg (',' arg)*
    arg       := [name ':'] value
    value     := atom ['<' mode] ['#' tag]
    atom      := number | ident | '$' ref | ident '|' qualifier

- `$0`, `$1`, ... are positional parameters filled in by the caller (a skill
  or an item), `$name` a named one.
- `mode` is `absolute` or `relative` and says how the value applies.
- `#tag` is a UI or localisation tag; `#~` means "none".
- `|qualifier` narrows an identifier (a stack count or variant).
- A name may itself be a parameter slot, as in `$0:1.0<relative`.

Parsing happens here, offline, so the runtime only ever executes structured
data instead of re-parsing text every tick.
"""
import re
import sqlite3
from dataclasses import dataclass, field

ABSOLUTE, RELATIVE = "absolute", "relative"
NO_TAG = "~"

# Columns whose content is the modifier language.
MODIFIER_COLUMNS = ("StartModifiers", "TickModifiers", "StopModifiers",
                    "DoneModifiers", "OnResumeStartModifiers")

_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")


@dataclass
class Value:
    """One modifier argument."""
    raw: str
    number: float = None
    ident: str = None
    param: str = None          # '$0' -> '0', '$dmg' -> 'dmg'
    mode: str = None           # 'absolute' / 'relative'
    tag: str = None            # from '#tag' ('~' meaning none)
    qualifier: str = None      # from '|N'

    @property
    def is_param(self):
        return self.param is not None

    def resolve(self, params):
        """Numeric value, substituting `$refs` from `params`."""
        if self.param is not None:
            v = params.get(self.param)
            return float(v) if v is not None else 0.0
        return self.number if self.number is not None else 0.0


@dataclass
class Modifier:
    verb: str
    args: list = field(default_factory=list)     # positional Values
    named: dict = field(default_factory=dict)    # name -> Value
    raw: str = ""

    def arg(self, index, default=None):
        return self.args[index] if index < len(self.args) else default

    @property
    def mode(self):
        """Mode carried by the first argument, the usual place for it."""
        for a in self.args:
            if a.mode:
                return a.mode
        return None


def parse_value(text):
    """Parse one argument into a Value."""
    raw = text
    tag = None
    if "#" in text:
        text, tag = text.split("#", 1)
    mode = None
    if "<" in text:
        text, mode = text.split("<", 1)
    qualifier = None
    if "|" in text:
        text, qualifier = text.split("|", 1)
    text = text.strip()

    v = Value(raw=raw, mode=mode or None, tag=tag, qualifier=qualifier)
    if text.startswith("$"):
        v.param = text[1:]
    elif _NUMBER.match(text):
        v.number = float(text)
    elif text:
        v.ident = text
    return v


def parse_modifiers(text):
    """Parse a modifier field into a list of Modifier.

    Unparseable fragments are skipped rather than raising: the point is to
    keep loading the 14,000-odd usages even if one row is malformed.
    """
    out = []
    if not text:
        return out
    for part in str(text).split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        verb, rest = part.split(":", 1)
        verb = verb.strip()
        if not verb:
            continue
        mod = Modifier(verb=verb, raw=part)
        for chunk in rest.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            # A named argument is 'name:value'. The name may be a parameter
            # slot ($0), the value may carry its own mode ('1.0<relative').
            if ":" in chunk:
                name, val = chunk.split(":", 1)
                mod.named[name.strip()] = parse_value(val)
            else:
                mod.args.append(parse_value(chunk))
        out.append(mod)
    return out


@dataclass
class EffectRef:
    """One entry of a skill's UserStatusEffects / VictimStatusEffects list.

    Same argument grammar as a modifier but with no verb: the first token is
    the status-effect id, the rest are named parameters.

        effect_id, C:chance, D:duration, DP:duration_pvp, ON:trigger, $0:value

    The `$n` entries are what fill the `$n` references inside that effect's
    own modifiers -- this is the parameter-passing mechanism that ties a skill
    to the numbers its effects apply.
    """
    effect_id: str = ""
    chance: float = 1.0
    duration: float = None
    duration_pvp: float = None
    trigger: str = None
    params: dict = field(default_factory=dict)   # '0' -> Value
    named: dict = field(default_factory=dict)    # everything else, verbatim
    raw: str = ""


def parse_effect_list(text):
    """Parse a skill's status-effect application list."""
    out = []
    if not text:
        return out
    for part in str(text).split(";"):
        part = part.strip()
        if not part:
            continue
        chunks = [c.strip() for c in part.split(",") if c.strip()]
        if not chunks:
            continue
        ref = EffectRef(raw=part)
        first = parse_value(chunks[0])
        ref.effect_id = first.ident or first.raw
        for chunk in chunks[1:]:
            if ":" not in chunk:
                continue
            name, val = chunk.split(":", 1)
            name, value = name.strip(), parse_value(val)
            key = name.upper()
            if name.startswith("$"):
                ref.params[name[1:]] = value
            elif key == "C":
                ref.chance = value.number if value.number is not None else 1.0
            elif key == "D":
                ref.duration = value.number
            elif key == "DP":
                ref.duration_pvp = value.number
            elif key in ("ON", "OFF"):
                ref.trigger = (value.ident or value.raw)
                ref.named[key] = value
            else:
                ref.named[name] = value
        out.append(ref)
    return out


def connect(path):
    """Open the database read-only; it is client data and stays untouched."""
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def table_names(db):
    return [r[0] for r in db.execute(
        "select name from sqlite_master where type='table' order by name")]


def columns(db, table):
    return [r[1] for r in db.execute(f'pragma table_info("{table}")')]


def rows(db, table, only=None):
    """Yield rows of `table` as dicts, optionally restricted to `only` columns."""
    cols = columns(db, table)
    pick = [c for c in cols if c in only] if only else cols
    quoted = ", ".join(f'"{c}"' for c in pick)
    for row in db.execute(f'select {quoted} from "{table}"'):
        yield dict(zip(pick, row))


def load_attributes(db):
    """The stat registry: name -> {type, read_write, dynamic}."""
    out = {}
    for r in rows(db, "_Attributes"):
        out[r["AttrName"]] = {"type": r.get("AttrType"),
                              "read_write": r.get("AttrReadWrite"),
                              "dynamic": r.get("AttrDynamic")}
    return out


def load_status_effects(db):
    """Status effects, with every modifier column parsed."""
    out = {}
    for r in rows(db, "_Template_StatusEffect"):
        eid = r.get("Id")
        if not eid:
            continue
        r["_modifiers"] = {c: parse_modifiers(r.get(c))
                           for c in MODIFIER_COLUMNS if r.get(c)}
        out[eid] = r
    return out


def load_skills(db, char_class=None):
    """Skills, keyed by Id. `char_class` filters to one playable class.

    Most rows are monster skills with an empty CharClass; only about a
    hundred belong to the playable classes.
    """
    out = {}
    for r in rows(db, "_Template_Skill"):
        sid = r.get("Id")
        if not sid:
            continue
        if char_class is not None and (r.get("CharClass") or "") != char_class:
            continue
        out[sid] = r
    return out


def verb_histogram(db):
    """Count modifier verbs across every modifier column of every table.

    Used to decide which verbs an interpreter has to cover first.
    """
    counts = {}
    for table in table_names(db):
        cols = [c for c in columns(db, table) if c.endswith("Modifiers")]
        if not cols:
            continue
        for r in rows(db, table, only=set(cols)):
            for c in cols:
                for m in parse_modifiers(r.get(c)):
                    counts[m.verb] = counts.get(m.verb, 0) + 1
    return counts
