class CfgPatches {
    class arma3_mcp_main {
        name = "Arma 3 MCP";
        units[] = {};
        weapons[] = {};
        requiredVersion = 2.18;  // toJSON / fromJSON
        requiredAddons[] = {"A3_Functions_F"};
        author = "headswe";
        version = "0.1";
        versionStr = "0.1.0";
        versionAr[] = {0,1,0};
    };
};

class CfgFunctions {
    class a3mcp {
        tag = "a3mcp";
        class main {
            file = "\z\arma3_mcp\addons\main\functions";
            class init { postInit = 1; };
            class drain {};
            class handle {};
            class reply {};
            class target {};
            class remoteResult {};
            class execute { headerType = -1; };    // sent to other machines as code, so no function header
            class remoteRun { headerType = -1; };  // sent to other machines as code, so no function header
        };
    };
};

// A machine that ran a request for the bridge (target = a client) sends the answer back with
// a3mcp_fnc_remoteResult. Allowed here for servers that whitelist remote execution; a mission
// with its own CfgRemoteExec in whitelist mode must list it too.
class CfgRemoteExec {
    class Functions {
        class a3mcp_fnc_remoteResult { allowedTargets = 0; jip = 0; };
    };
};
