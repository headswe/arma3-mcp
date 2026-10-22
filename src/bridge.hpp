// The TCP side of the extension: MCP clients connect, send SQF requests, and get the game's
// answers back. The game picks requests up through callExtension (see extension.cpp).
//
// Wire protocol, one line per message, "\n" terminated:
//
//   client -> extension                      extension -> client
//   AUTH <token>                             OK auth | ERR <reason>   (first line, when a token is set)
//   PING <id>                                PONG <id>
//   STATUS <id>                              STATUS <id> <json>
//   EXEC <id> <json request>                 RESULT <id> <json> | ERROR <id> <reason>
//   CANCEL <id>                              (drops a request the game has not picked up yet)
//
// Any other line closes the connection, so a browser that POSTs to the port (its request line
// comes first) cannot get an EXEC in through the body.
//
// <id> is chosen by the client and only has to be unique on its connection.
#pragma once

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <filesystem>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <utility>

namespace a3mcp {

struct config {
    std::string host = "127.0.0.1";
    int port = 9120;
    std::string token;
    bool enabled = true;
    bool allow_client = false;  // also listen on machines that are not the server
};

// Reads <dir>/arma3_mcp.ini (key = value, # comments), then ARMA3_MCP_* environment variables.
config load_config(const std::filesystem::path& dir);

// Undoes callExtension's quoting of string arguments: "a""b" -> a"b.
std::string unquote_sqf(std::string_view s);

// Longest prefix of s no longer than max bytes that does not cut a UTF-8 sequence.
size_t utf8_prefix(std::string_view s, size_t max);

class connection;

class bridge {
public:
    using wake_fn = std::function<bool()>;  // returns false if the game could not be notified

    // Starts listening; idempotent. Returns a line for the RPT.
    bool start(const config& cfg, std::string& message);
    bool listening() const { return listening_.load(); }

    // Called when a request is queued so the game drains the queue now instead of on its next poll.
    void set_wake(wake_fn fn);

    // ---- game thread ----
    // Takes the next request off the queue: its id and payload size in bytes.
    std::optional<std::pair<std::string, size_t>> poll();
    // Copies the next piece of a polled request's payload. more = bytes remain after it.
    bool read(const std::string& id, size_t max, std::string& chunk, bool& more);
    // Appends to / sends the response of a polled request.
    bool append(const std::string& id, std::string_view chunk);
    bool finish(const std::string& id);
    // Fails every request the game has picked up but not answered (the mission ended).
    void reset(const std::string& reason);
    // Notes what kind of machine started the bridge, for STATUS.
    void set_game_info(std::string json);

    std::string status_json();

private:
    struct request {
        std::string id;         // ours, unique for the process
        std::string client_id;  // the client's, unique on its connection
        std::weak_ptr<connection> conn;
        std::string payload;
        size_t offset = 0;
        std::string response;
    };

    void accept_loop();
    void serve(std::shared_ptr<connection> conn);
    // Returns false when the connection should be closed.
    bool handle_line(const std::shared_ptr<connection>& conn, std::string_view line);
    void wake();

    std::mutex mutex_;
    std::deque<std::shared_ptr<request>> queue_;
    std::unordered_map<std::string, std::shared_ptr<request>> active_;
    uint64_t next_id_ = 1;
    std::chrono::steady_clock::time_point last_poll_{};
    bool polled_ = false;
    uint64_t served_ = 0;
    int connections_ = 0;
    std::string game_info_ = "null";

    wake_fn wake_fn_;
    bool signaled_ = false;  // a wake-up is outstanding; cleared when the game finds the queue empty

    config cfg_;
    std::atomic<bool> listening_{false};
    std::intptr_t listen_socket_ = -1;
};

}  // namespace a3mcp
