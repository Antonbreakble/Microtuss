(* One CODESYS object per file is the usual delivery format; these three files
   are a miniature project in that shape. *)

TYPE
    E_Fill : (Idle, Filling, Full, Overflow);
END_TYPE

TYPE
    T_Limits : STRUCT
        Lo : REAL := 10.0;              (* defaults are part of the type *)
        Hi : REAL := 90.0;
    END_STRUCT;
END_TYPE
