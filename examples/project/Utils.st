(* The project's own utility library. *)

(* Written here in ST, this TON replaces the interpreter's built-in one: a POU
   found in the sources always wins over the standard library.  That is what
   makes it possible to test a block against the same timer it ships with. *)
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


FUNCTION Pct : REAL
(* Where v sits between lo and hi, in percent.

   The bounds carry defaults, so a caller may leave them out -- which is how a
   FUNCTION's own defaults are exercised from Python (see "Вызов FUNCTION"). *)
VAR_INPUT
    v : REAL;
    lo : REAL := 0.0;
    hi : REAL := 100.0;
END_VAR
    IF hi = lo THEN
        Pct := 0.0;
        RETURN;
    END_IF;
    Pct := (v - lo) * 100.0 / (hi - lo);
END_FUNCTION
