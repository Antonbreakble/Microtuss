(* DUTs -- user-defined data types, as CODESYS exports them into a .st file. *)

TYPE
    E_DriveState : (Idle, Starting, Running, Stopping, Faulted);
    E_OpMode : (
        Off := 0,
        Manual := 10,
        Automatic := 20,
        Service := 30
    );
END_TYPE

TYPE
    T_Range : STRUCT
        Lo : REAL;
        Hi : REAL;
    END_STRUCT;
END_TYPE

TYPE
    T_DriveConfig : STRUCT
        Mode : E_OpMode;
        SpeedRange : T_Range;
        RampTime : TIME;
        Name : STRING[16];
    END_STRUCT;
END_TYPE

TYPE
    T_DriveConfigs : ARRAY[1..2] OF T_DriveConfig;
END_TYPE
