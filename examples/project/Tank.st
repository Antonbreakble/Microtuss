FUNCTION_BLOCK FB_Tank
VAR_INPUT
    Fill : BOOL;
    Level : REAL := 50.0;
    Limits : T_Limits;
END_VAR
VAR_INPUT CONSTANT
    SettleTime : TIME := T#300ms;       (* how long "high" must persist *)
END_VAR
VAR_OUTPUT
    State : E_Fill;
    Percent : REAL;                     (* the level, as a percentage of range *)
    Overflowing : BOOL;
END_VAR
VAR
    settle : TON;
END_VAR
    Percent := Pct(v := Level, lo := Limits.Lo, hi := Limits.Hi);
    Overflowing := Fill AND (Level >= Limits.Hi);

    (* Full is only believed once the level has stayed high for SettleTime. *)
    settle(IN := Level >= Limits.Hi, PT := SettleTime);

    IF NOT Fill THEN
        State := E_Fill#Idle;
    ELSIF Level < Limits.Lo THEN
        State := E_Fill#Filling;
    ELSIF settle.Q THEN
        State := E_Fill#Full;
    ELSE
        State := E_Fill#Filling;
    END_IF;
END_FUNCTION_BLOCK
