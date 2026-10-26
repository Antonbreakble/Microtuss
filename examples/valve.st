(* A small block used by the examples in this folder.

   It is deliberately ordinary: a command input, a travel timer, and two
   outputs.  Copy the shape, not the logic. *)

TYPE
    E_ValveState : (Closed, Open);
END_TYPE

FUNCTION_BLOCK FB_Valve
VAR_INPUT
    Cmd : BOOL;                         (* TRUE = open, FALSE = close *)
    TravelTime : TIME := T#2s;
END_VAR
VAR_OUTPUT
    Open : BOOL;
    Busy : BOOL;                        (* the valve is still travelling *)
    State : E_ValveState;
END_VAR
VAR
    travel : TON;
    opened : BOOL;
END_VAR
    (* The timer runs for as long as the command and the position disagree. *)
    travel(IN := Cmd <> opened, PT := TravelTime);
    IF travel.Q THEN
        opened := Cmd;                  (* travel finished *)
    END_IF;

    Open := opened;
    Busy := Cmd <> opened;

    IF opened THEN
        State := E_ValveState#Open;
    ELSE
        State := E_ValveState#Closed;
    END_IF;
END_FUNCTION_BLOCK
