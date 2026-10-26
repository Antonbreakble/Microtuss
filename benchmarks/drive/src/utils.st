(* A stand-in for a project's utils library.

   The point of interest is TON: it is a FUNCTION_BLOCK written in Structured
   Text that shadows the interpreter's built-in implementation of the same
   name.  Nothing special is needed to make that happen -- a POU found in the
   sources always wins over the standard library. *)

FUNCTION_BLOCK TON
VAR_INPUT
    IN : BOOL;
    PT : TIME;
END_VAR
VAR_OUTPUT
    Q : BOOL;
    ET : TIME;
END_VAR
VAR
    _StartedAt : TIME;
    _Timing : BOOL;
END_VAR
    IF NOT IN THEN
        Q := FALSE;
        ET := T#0s;
        _Timing := FALSE;
        RETURN;
    END_IF;

    IF NOT _Timing THEN
        _StartedAt := TIME();
        _Timing := TRUE;
    END_IF;

    ET := TIME() - _StartedAt;
    IF ET >= PT THEN
        Q := TRUE;
        ET := PT;
    ELSE
        Q := FALSE;
    END_IF;
END_FUNCTION_BLOCK


FUNCTION FB_Scale : REAL
(* Map v from [inLo, inHi] onto [outLo, outHi], clamped at both ends. *)
VAR_INPUT
    v : REAL;
    inLo : REAL;
    inHi : REAL;
    outLo : REAL;
    outHi : REAL;
END_VAR
VAR
    span : REAL;
END_VAR
    IF inHi = inLo THEN
        FB_Scale := outLo;
        RETURN;
    END_IF;

    span := inHi - inLo;
    v := LIMIT(inLo, v, inHi);
    FB_Scale := outLo + (v - inLo) * (outHi - outLo) / span;
END_FUNCTION


FUNCTION FB_ClampRange : REAL
VAR_INPUT
    v : REAL;
    r : T_Range;
END_VAR
    FB_ClampRange := LIMIT(r.Lo, v, r.Hi);
END_FUNCTION
