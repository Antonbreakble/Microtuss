"""Symbol resolution: turns parsed source units into an executable model.

The model owns the type table (built-ins plus every ``TYPE`` declared in the
loaded sources), the POU table, the global variable space, and the standard
function library.  Lookup is case-insensitive because the lexer already
upper-cases every identifier.
"""

from __future__ import annotations

from typing import Any, Callable

from . import ast_nodes as A
from .errors import IecError, IecNameError, IecTypeError
from .values import (
    ANY,
    ARRAY,
    BUILTIN_TYPES,
    ENUM,
    FB,
    POINTER,
    REFERENCE,
    STRING,
    STRUCT,
    ArrayValue,
    Cell,
    IecType,
    coerce,
    default_value,
)


class _UnresolvedType(IecTypeError):
    """Internal: a DUT is referenced before it has been resolved."""


class PouEntry:
    """A resolved POU: its declaration plus the scope it was found in."""

    __slots__ = ("defn", "type", "locals")

    def __init__(self, defn: A.PouDef, type_: IecType, locals_: dict[str, Cell]) -> None:
        self.defn = defn
        self.type = type_
        self.locals = locals_

    @property
    def name(self) -> str:
        return self.defn.name

    @property
    def kind(self) -> str:
        """``FUNCTION``, ``FUNCTION_BLOCK`` or ``PROGRAM`` -- what it was declared as."""
        return self.defn.kind


class Model:
    def __init__(self) -> None:
        self.types: dict[str, IecType] = dict(BUILTIN_TYPES)
        self.pous: dict[str, PouEntry] = {}
        self.globals: dict[str, Cell] = {}
        self.units: list[A.SourceUnit] = []
        self.stdlib: dict[str, object] = {}
        self._const_literals: dict[str, Any] = {}

    # -- lookup helpers ----------------------------------------------------

    def resolve_type(self, spec: A.TypeSpec, source: str = "<st>") -> IecType:
        """Resolve a syntactic type reference to a concrete :class:`IecType`."""
        name = spec.name.upper()

        if name == "ARRAY":
            elem = self.resolve_type(spec.elem, source) if spec.elem else self.types["ANY"]
            bounds: list[tuple[int, int]] = []
            for lo_expr, hi_expr in spec.ranges:
                bounds.append((self._const_int(lo_expr, source), self._const_int(hi_expr, source)))
            return IecType("ARRAY", ARRAY, elem=elem, bounds=tuple(bounds))

        if spec.ref or name == "REFERENCE":
            target = self.resolve_type(spec.elem, source) if spec.elem else self.types["ANY"]
            return IecType(target.describe(), REFERENCE, target=target)

        if spec.ptr or name == "POINTER":
            target = self.resolve_type(spec.elem, source) if spec.elem else self.types["ANY"]
            return IecType(f"POINTER TO {target.describe()}", POINTER, target=target)

        if name == "STRUCT" or name == "UNION":
            fields: dict[str, IecType] = {}
            for decl in spec.fields or []:
                ftype = self.resolve_type(decl.type, source)
                for fname in decl.names:
                    fields[fname] = ftype
            return IecType(name, STRUCT, fields=fields)

        if name in ("STRING", "WSTRING"):
            return IecType(name, STRING, length=spec.length)

        if spec.members is not None:
            # An enumeration written inline on a variable instead of as a named
            # DUT.  It has no name of its own, so key the cache on everything
            # that makes it distinct; two identical declarations may share one.
            key = "ENUM$" + ";".join(
                f"{m.name}={(m.value.value if isinstance(m.value, A.Literal) else '?')}"
                for m in spec.members
            )
            cached = self.types.get(key)
            if cached is not None:
                return cached
            self.types[key] = self._build_enum(key, spec.members, spec.base)
            return self.types[key]

        if name in self.types:
            return self.types[name]

        # A DUT that is queued for resolution may yet turn out to exist once
        # its own dependencies are in place, so the caller retries.
        if name in getattr(self, "_unresolved", ()):
            raise _UnresolvedType(name)
        raise IecTypeError(f"unknown type '{spec.name}'", source=source)

    def _const_int(self, expr: A.Expr, source: str) -> int:
        if isinstance(expr, A.Literal) and isinstance(expr.value, (int, bool)):
            return int(expr.value)
        if isinstance(expr, A.Unary) and expr.op == "-" and isinstance(expr.operand, A.Literal):
            return -int(expr.operand.value)  # type: ignore[arg-type]
        # Bounds given as named constants are resolved after globals exist.
        if isinstance(expr, A.VarRef):
            cell = self.globals.get(expr.name)
            if cell is not None and isinstance(cell.value, int):
                return cell.value
        raise IecTypeError("array bounds must be constant integers", source=source)

    def type_of(self, name: str) -> IecType | None:
        return self.types.get(name.upper())

    def pou(self, name: str) -> PouEntry | None:
        return self.pous.get(name.upper())

    # -- loading -----------------------------------------------------------

    def add_unit(self, unit: A.SourceUnit) -> None:
        self.units.append(unit)

    def link(self) -> None:
        """Resolve every TYPE.  Runs repeatedly, because a DUT in one file may
        be built from a DUT in another."""
        pending: list[A.TypeAlias | A.StructDef | A.EnumDef] = [
            decl for unit in self.units for decl in unit.types
        ]
        declared = {d.name for d in pending}

        while pending:
            progressed = False
            deferred: list[A.TypeAlias | A.StructDef | A.EnumDef] = []
            for decl in pending:
                try:
                    self._declare_type(decl)
                except _UnresolvedType:
                    # A type referenced from another file that has not been
                    # declared yet; retry after the next sweep.
                    deferred.append(decl)
                    continue
                progressed = True
            if not progressed:
                stuck = deferred[0].name
                if stuck in declared:
                    raise IecTypeError(
                        f"type '{stuck}' could not be resolved -- check for a "
                        f"recursive definition or a missing DUT"
                    )
                raise IecTypeError(f"unknown type '{stuck}'")
            pending = deferred

    def _declare_type(self, decl: A.TypeAlias | A.StructDef | A.EnumDef) -> None:
        if isinstance(decl, A.TypeAlias):
            self._unresolved.add(decl.name)
            self._tentative(decl.name, lambda: self.resolve_type(decl.type))
        elif isinstance(decl, A.StructDef):
            def build_struct() -> IecType:
                fields: dict[str, IecType] = {}
                defaults: dict[str, Any] = {}
                for vdecl in decl.fields:
                    ftype = self.resolve_type(vdecl.type)
                    for fname in vdecl.names:
                        fields[fname] = ftype
                        # ``InLow : REAL := 4`` is part of the DUT's definition:
                        # a parameter struct comes up holding its declared
                        # defaults, not zeros.
                        if vdecl.init is not None:
                            defaults[fname] = self._const_value(vdecl.init, ftype)
                return IecType(decl.name, STRUCT, fields=fields, field_defaults=defaults)

            self._unresolved.add(decl.name)
            self._tentative(decl.name, build_struct)
        elif isinstance(decl, A.EnumDef):
            self.types[decl.name] = self._build_enum(decl.name, decl.members, decl.base)

    def _const_value(self, expr: A.Expr, target: IecType) -> Any:
        """Evaluate a DUT member's default.

        Defaults are restricted to literals and named constants: a struct
        definition is built while the model is still being linked, so there is
        no instance to run arbitrary expressions against.
        """
        if isinstance(expr, A.Literal):
            return expr.value
        if isinstance(expr, A.Unary) and expr.op == "-" and isinstance(expr.operand, A.Literal):
            return -expr.operand.value
        if isinstance(expr, A.VarRef):
            # Types are linked before VAR_GLOBAL is declared, so a constant is
            # looked up in the pre-scanned literals first.
            if expr.name in self._const_literals:
                return self._const_literals[expr.name]
            const = self.globals.get(expr.name)
            if const is not None:
                return const.value
            # A member of the target enumeration, written the CODESYS way:
            # ``Mode : E_OpMode := Automatic``.
            while target.kind == ARRAY and target.elem is not None:
                target = target.elem
            if target.kind == ENUM and expr.name in (target.members or {}):
                return target.members[expr.name]
        raise IecTypeError(
            f"a default value must be a constant, got {type(expr).__name__}",
            source=str(expr.pos),
        )

    def _build_enum(
        self, name: str, decls: list[A.EnumMember], base_spec: A.TypeSpec | None
    ) -> IecType:
        """Turn a list of members into an ENUM type.

        A member without an explicit value takes one more than its predecessor,
        so ``(Idle, Auto, Manual := 5, Fault)`` gives Fault the value 6.
        """
        base = self.types["INT"]
        if base_spec is not None:
            base = self.resolve_type(base_spec)
        members: dict[str, int] = {}
        nxt = 0
        for member in decls:
            if member.value is not None:
                lit = member.value
                if not isinstance(lit, A.Literal) or not isinstance(lit.value, int):
                    raise IecTypeError(
                        f"enumeration member '{member.name}' needs a constant integer value",
                        source=str(member.pos),
                    )
                nxt = int(lit.value)
            members[member.name] = nxt
            nxt += 1
        return IecType(
            name, ENUM, bits=base.bits or 16, signed=base.signed, members=members, base=base
        )

    def _tentative(self, name: str, build: Callable[[], IecType]) -> None:
        """Resolve a user type, allowing forward references to other DUTs.

        If the type is still unresolved after every other DUT has had a turn,
        :meth:`link` reports it -- which is how a self-referential STRUCT is
        caught rather than silently left half-built.
        """
        self._resolving.add(name)
        try:
            self.types[name] = build()
            self._unresolved.discard(name)
        finally:
            self._resolving.discard(name)

    def build(self) -> None:
        """Link the types, then declare globals, POUs and their variables."""
        self._resolving: set[str] = set()
        # Names of user DUTs that still need resolving; while one of them is on
        # this set, a reference to it is deferred rather than reported missing.
        self._unresolved: set[str] = set()
        # Literal-valued globals, collected before linking so a DUT default may
        # name a constant declared in another file.
        self._const_literals: dict[str, Any] = {}
        for unit in self.units:
            for decl in unit.globals:
                if isinstance(decl.init, A.Literal):
                    for vname in decl.names:
                        self._const_literals[vname] = decl.init.value
        self.link()

        for unit in self.units:
            for decl in unit.globals:
                self._declare_vars(decl, self.globals)
        self._declare_enum_members()

        for unit in self.units:
            for pou in unit.pous:
                if pou.kind == "FUNCTION":
                    ret = self.resolve_type(pou.return_type) if pou.return_type else self.types["ANY"]
                    entry = PouEntry(pou, IecType(pou.name, "function", target=ret), {})
                    entry.type.impl = pou
                    self.pous[pou.name] = entry
                elif pou.kind in ("FUNCTION_BLOCK", "PROGRAM"):
                    t = IecType(pou.name, FB)
                    t.pou = pou
                    entry = PouEntry(pou, t, {})
                    entry.type.impl = pou
                    self.pous[pou.name] = entry
                    # A POU must also be reachable as a *type*, so that
                    # ``VAR inst : SomeBlock; END_VAR`` resolves.  An explicit
                    # TYPE of the same name wins.
                    self.types.setdefault(pou.name, t)

        self._load_stdlib()
        # Variables are resolved lazily at instantiation, so a POU referencing
        # a misspelled DUT would otherwise only fail when a test ran it.
        self._check_pou_types()

    def _declare_enum_members(self) -> None:
        """Make enumeration members visible by their bare names.

        IEC 61131-3 puts every enum member in the global namespace, so both
        ``Mode := E_Mode#Auto`` and ``Mode := Auto`` are valid, and CODESYS
        code uses the short form heavily.  A real global of the same name wins.
        """
        for t in list(self.types.values()):
            if t.kind != ENUM:
                continue
            for name, ordinal in (t.members or {}).items():
                self.globals.setdefault(name, Cell(ordinal, t, name))

    def _check_pou_types(self) -> None:
        for entry in self.pous.values():
            for decl in entry.defn.vars:
                self.resolve_type(decl.type, source=str(decl.pos))
            ret = entry.defn.return_type
            if ret is not None:
                self.resolve_type(ret, source=str(entry.defn.pos))

    def _declare_vars(self, decl: A.VarDecl, into: dict[str, Cell]) -> None:
        t = self.resolve_type(decl.type)
        store_t = self._storage_type(t, decl.section)
        for name in decl.names:
            cell = Cell(default_value(store_t), store_t, name)
            if decl.init is not None and store_t.kind != FB:
                # ``C_MAX : INT := 77`` and ``Arr : ARRAY[1..3] OF INT := [1,2,3]``
                # start out holding that value.  An FB instance is left alone:
                # its own initialisers have already run in instantiate().
                if isinstance(decl.init, A.ArrayLiteral):
                    cell.value = self._const_array(decl.init, store_t)
                else:
                    cell.value = coerce(self._const_value(decl.init, store_t), store_t)
            into[name] = cell

    def _const_array(self, node: A.ArrayLiteral, target: IecType) -> Any:
        """Build an array constant, e.g. ``[1, 2, 3]`` in a VAR initialiser."""
        if target.kind != ARRAY:
            raise IecTypeError("an array initialiser needs an ARRAY type",
                               source=str(node.pos))
        value = ArrayValue(target)
        value.build([self._const_value(item, target.elem) for item in node.items])
        return value

    def _storage_type(self, t: IecType, section: str) -> IecType:
        """VAR_IN_OUT holds a reference, not a copy, so its storage differs."""
        if section == "inout":
            return IecType(t.describe(), REFERENCE, target=t)
        return t

    def _load_stdlib(self) -> None:
        from .stdlib import STANDARD_FUNCTIONS, standard_fb_types

        self.stdlib.update(STANDARD_FUNCTIONS)
        # Registered as types so that ``ton1 : TON;`` resolves; a user
        # declaration of the same name wins, hence setdefault.
        for name, t in standard_fb_types().items():
            self.types.setdefault(name, t)


def load_sources(sources: dict[str, str], model: Model | None = None) -> Model:
    """Parse and link *sources* (filename to text) into a :class:`Model`."""
    from .parser import parse_text

    model = model or Model()
    units = [parse_text(text, name) for name, text in sources.items()]
    for unit in units:
        model.add_unit(unit)
    model.build()
    return model


def validate(model: Model) -> list[IecError]:
    """Cheap sanity pass: report POUs whose variables reference unknown types."""
    problems: list[IecError] = []
    for entry in model.pous.values():
        defn = entry.defn
        for decl in getattr(defn, "vars", []):
            try:
                model.resolve_type(decl.type)
            except IecError as exc:
                problems.append(exc)
        if defn.extends and model.pou(defn.extends) is None and model.type_of(defn.extends) is None:
            problems.append(IecNameError(f"base type '{defn.extends}' is not declared"))
    return problems
