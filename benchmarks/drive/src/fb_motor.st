(* Example source used as a parser fixture.  Deliberately exercises the
   awkward corners of the language: enumerations, a struct type, an array
   initialiser, CASE, FOR/WHILE/REPEAT, pointer dereference, a standard
   function block call and a user function. *)

TYPE
    E_State : (Idle, Running := 10, Fault);
    T_Point : STRUCT
        x : REAL;
        y : REAL;
    END_STRUCT;
    T_Arr : ARRAY[0..3] OF T_Point;
END_TYPE

VAR_GLOBAL
    g_Limit : INT := 100;
    g_Enable : BOOL := TRUE;
END_VAR

FUNCTION_BLOCK FB_Motor
VAR_INPUT
    Start : BOOL;
    Stop : BOOL;
    Setpoint : INT := 50;
END_VAR
VAR_IN_OUT
    Position : INT;
END_VAR
VAR_OUTPUT
    Running : BOOL;
    Speed : INT;
    Err : INT;
END_VAR
VAR
    state : E_State := E_State#Idle;
    pt : T_Point;
    arr : ARRAY[1..8] OF INT := [1,2,3,4,5,6,7,8];
    ton1 : TON;
    i : INT;
    sum : INT;
    p : POINTER TO INT;
END_VAR
    IF Start AND NOT Stop THEN
        state := E_State#Running;
    ELSIF Stop THEN
        state := E_State#Idle;
    END_IF;

    CASE state OF
        E_State#Idle:
            Running := FALSE;
            Speed := 0;
        E_State#Running:
            Running := TRUE;
            Speed := Setpoint;
            Position := Position + 1;
        ELSE
            Err := 1;
    END_CASE;

    sum := 0;
    FOR i := 1 TO 8 BY 1 DO
        sum := sum + arr[i];
        IF sum > g_Limit THEN
            EXIT;
        END_IF;
    END_FOR;

    WHILE Position < 10 DO
        Position := Position + 1;
    END_WHILE;

    REPEAT
        sum := sum - 1;
    UNTIL sum <= 0 END_REPEAT;

    pt.x := 1.5;
    p := ADR(sum);
    p^ := p^ + 1;

    ton1(IN := Running, PT := T#500ms);
    Speed := INT(ton1.ET / T#1ms);

    Speed := F_Clamp(v := Speed, lo := 0, hi := 1000);
END_FUNCTION_BLOCK

FUNCTION F_Clamp : INT
VAR_INPUT
    v : INT;
    lo : INT;
    hi : INT;
END_VAR
    IF v < lo THEN
        F_Clamp := lo;
    ELSIF v > hi THEN
        F_Clamp := hi;
    ELSE
        F_Clamp := v;
    END_IF;
END_FUNCTION
