(* A function block under test that uses both the project's DUTs and utils. *)

FUNCTION_BLOCK FB_Drive
VAR_INPUT
    Enable : BOOL;
    Reset : BOOL;
    Cfg : T_DriveConfig;
    Feedback : REAL;
END_VAR
VAR_OUTPUT
    State : E_DriveState;
    Speed : REAL;
    AtTarget : BOOL;
    Faulted : BOOL;
END_VAR
VAR
    startTimer : TON;
    faultTimer : TON;
    configs : T_DriveConfigs;
    activeConfig : T_DriveConfig;
END_VAR
    (* Keep the whole configuration array in sync, then work from the copy. *)
    configs[1] := Cfg;
    configs[2] := Cfg;
    activeConfig := configs[1];

    IF Reset THEN
        (* Reset owns the whole scan: without the RETURN the drive would start
           ramping again on the very scan that cleared the fault. *)
        State := E_DriveState#Idle;
        Speed := 0.0;
        AtTarget := FALSE;
        Faulted := FALSE;
        startTimer(IN := FALSE, PT := activeConfig.RampTime);
        faultTimer(IN := FALSE, PT := T#5s);
        RETURN;
    END_IF;

    IF NOT Enable THEN
        (* Stopping takes two scans: the one that sees the command, and the one
           that completes the stop.  This branch returns, so the transition to
           Idle has to happen here rather than in the CASE below. *)
        IF State = E_DriveState#Stopping THEN
            State := E_DriveState#Idle;
        ELSIF State <> E_DriveState#Idle THEN
            State := E_DriveState#Stopping;
        END_IF;
        Speed := 0.0;
        startTimer(IN := FALSE, PT := activeConfig.RampTime);
        faultTimer(IN := FALSE, PT := T#5s);
        RETURN;
    END_IF;

    (* A drive left running with no feedback is a fault. *)
    faultTimer(IN := (State = E_DriveState#Running) AND (ABS(Feedback) < 0.1),
               PT := T#500ms);
    IF faultTimer.Q THEN
        State := E_DriveState#Faulted;
        Speed := 0.0;
        Faulted := TRUE;
        RETURN;
    END_IF;

    CASE State OF
        E_DriveState#Idle:
            startTimer(IN := TRUE, PT := activeConfig.RampTime);
            IF startTimer.Q THEN
                State := E_DriveState#Running;
            ELSE
                State := E_DriveState#Starting;
            END_IF;

        E_DriveState#Starting:
            startTimer(IN := TRUE, PT := activeConfig.RampTime);
            IF startTimer.Q THEN
                State := E_DriveState#Running;
            END_IF;

        E_DriveState#Running:
            (* Only automatic mode follows the feedback. *)
            IF activeConfig.Mode = E_OpMode#Automatic THEN
                Speed := Fb_ClampRange(v := Feedback, r := activeConfig.SpeedRange);
                AtTarget := ABS(Speed - Feedback) < 0.5;
            ELSE
                Speed := 0.0;
                AtTarget := FALSE;
            END_IF;

        E_DriveState#Stopping:
            Speed := 0.0;
            State := E_DriveState#Idle;

        ELSE
            Speed := 0.0;
    END_CASE;
END_FUNCTION_BLOCK
