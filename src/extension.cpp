// Arma 3 callExtension entry points for the MCP bridge.
//
//   "arma3_mcp" callExtension ["start", [isServer, hasInterface, isDedicated, missionName, worldName, eden]]
//        -> "listening on 127.0.0.1:9120" (return code 0), or why not (return code 1). Safe to call
//           every mission and editor event; eden is true while the Eden editor is open (not
//           previewing). Requests the previous mission picked up but never answered are failed.
//   "arma3_mcp" callExtension ["poll", []]           -> "" when idle, or "<id>:<bytes>" of the next request
//   "arma3_mcp" callExtension ["read", [id]]         -> the next piece of that request's JSON; return code 10
//                                                       while more follows, 0 on the last piece
//   "arma3_mcp" callExtension ["result", [id, text]] -> appends a piece of the response
//   "arma3_mcp" callExtension ["done", [id]]         -> sends the response to the MCP client
//   "arma3_mcp" callExtension ["status", []]         -> status JSON
//   "arma3_mcp" callExtension "version"
//
// When a request arrives the extension also fires the "ExtensionCallback" mission event
// ("arma3_mcp", "request", "") so the addon drains the queue on the next frame.

#include <cstring>
#include <filesystem>
#include <string>
#include <string_view>

#include "bridge.hpp"

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#define A3MCP_EXPORT __declspec(dllexport)
#define A3MCP_CALL __stdcall
#else
#include <dlfcn.h>
#define A3MCP_EXPORT __attribute__((visibility("default")))
#define A3MCP_CALL
#endif

using callback_fn = int (*)(const char* name, const char* function, const char* data);

extern "C" {
A3MCP_EXPORT void A3MCP_CALL RVExtensionVersion(char* output, unsigned int output_size);
A3MCP_EXPORT void A3MCP_CALL RVExtension(char* output, unsigned int output_size, const char* function);
A3MCP_EXPORT int A3MCP_CALL RVExtensionArgs(char* output, unsigned int output_size, const char* function,
                                            const char** argv, unsigned int argc);
A3MCP_EXPORT void A3MCP_CALL RVExtensionRegisterCallback(callback_fn callback);
}

namespace {

enum result_code { ok = 0, error = 1, unknown_function = 2, more_follows = 10 };

// Never destroyed: its threads run until the process exits, and joining threads from a DLL's
// static destructors can deadlock on shutdown.
a3mcp::bridge& bridge() {
    static auto* b = new a3mcp::bridge();
    return *b;
}

void set_output(char* output, unsigned int size, std::string_view text) {
    if (size == 0) return;
    size_t n = text.size() < size - 1 ? text.size() : size - 1;
    std::memcpy(output, text.data(), n);
    output[n] = '\0';
}

std::filesystem::path module_dir() {
#ifdef _WIN32
    HMODULE module = nullptr;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            reinterpret_cast<LPCWSTR>(&module_dir), &module))
        return {};
    wchar_t path[MAX_PATH * 4];
    DWORD n = GetModuleFileNameW(module, path, static_cast<DWORD>(sizeof(path) / sizeof(path[0])));
    if (n == 0) return {};
    return std::filesystem::path(std::wstring(path, n)).parent_path();
#else
    Dl_info info{};
    if (!dladdr(reinterpret_cast<void*>(&module_dir), &info) || !info.dli_fname) return {};
    return std::filesystem::path(info.dli_fname).parent_path();
#endif
}

std::string json_string(std::string_view s) {
    std::string out = "\"";
    for (char c : s) {
        if (c == '"' || c == '\\') out += '\\';
        if (static_cast<unsigned char>(c) >= 0x20) out += c;
    }
    return out + "\"";
}

int start(char* output, unsigned int size, const char** argv, unsigned int argc) {
    auto arg = [&](unsigned int i) { return i < argc ? a3mcp::unquote_sqf(argv[i]) : std::string(); };
    bool is_server = arg(0) == "true";
    bridge().set_game_info("{\"is_server\":" + std::string(is_server ? "true" : "false") +
                           ",\"has_interface\":" + (arg(1) == "true" ? "true" : "false") +
                           ",\"dedicated\":" + (arg(2) == "true" ? "true" : "false") + ",\"mission\":" + json_string(arg(3)) +
                           ",\"world\":" + json_string(arg(4)) +
                           ",\"eden\":" + (arg(5) == "true" ? "true" : "false") + "}");
    bridge().reset("the mission ended before the request finished");

    a3mcp::config cfg = a3mcp::load_config(module_dir());
    if (!cfg.enabled) {
        set_output(output, size, "disabled in arma3_mcp.ini / ARMA3_MCP_ENABLED");
        return error;
    }
    if (!is_server && !cfg.allow_client && !bridge().listening()) {
        set_output(output, size, "not the server; set allow_client = 1 to listen on this machine too");
        return error;
    }
    std::string message;
    bool listening = bridge().start(cfg, message);
    set_output(output, size, message);
    return listening ? ok : error;
}

}  // namespace

void A3MCP_CALL RVExtensionVersion(char* output, unsigned int output_size) {
    set_output(output, output_size, "arma3_mcp " A3MCP_VERSION);
}

void A3MCP_CALL RVExtension(char* output, unsigned int output_size, const char* function) {
    std::string_view fn(function ? function : "");
    if (fn == "version") {
        set_output(output, output_size, A3MCP_VERSION);
    } else if (fn == "status") {
        set_output(output, output_size, bridge().status_json());
    } else {
        set_output(output, output_size, "use the array syntax: \"arma3_mcp\" callExtension [function, [args]]");
    }
}

void A3MCP_CALL RVExtensionRegisterCallback(callback_fn callback) {
    bridge().set_wake([callback] { return callback("arma3_mcp", "request", "") >= 0; });
}

int A3MCP_CALL RVExtensionArgs(char* output, unsigned int output_size, const char* function, const char** argv,
                               unsigned int argc) {
    std::string_view fn(function ? function : "");
    set_output(output, output_size, "");

    if (fn == "poll") {
        auto head = bridge().poll();
        if (head) set_output(output, output_size, head->first + ":" + std::to_string(head->second));
        return ok;
    }
    if (fn == "read") {
        if (argc < 1 || output_size < 8) return error;
        std::string chunk;
        bool more = false;
        if (!bridge().read(a3mcp::unquote_sqf(argv[0]), output_size - 1, chunk, more)) return error;
        set_output(output, output_size, chunk);
        return more ? more_follows : ok;
    }
    if (fn == "result") {
        if (argc < 2) return error;
        return bridge().append(a3mcp::unquote_sqf(argv[0]), a3mcp::unquote_sqf(argv[1])) ? ok : error;
    }
    if (fn == "done") {
        if (argc < 1) return error;
        return bridge().finish(a3mcp::unquote_sqf(argv[0])) ? ok : error;
    }
    if (fn == "start") return start(output, output_size, argv, argc);
    if (fn == "status") {
        set_output(output, output_size, bridge().status_json());
        return ok;
    }
    if (fn == "version") {
        set_output(output, output_size, A3MCP_VERSION);
        return ok;
    }
    set_output(output, output_size, "unknown function");
    return unknown_function;
}
