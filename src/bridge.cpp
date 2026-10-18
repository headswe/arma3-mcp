#include "bridge.hpp"

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <thread>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
using socket_t = SOCKET;
#define A3MCP_BAD_SOCKET INVALID_SOCKET
#define A3MCP_SEND_FLAGS 0
static void close_socket(socket_t s) { closesocket(s); }
#else
#include <arpa/inet.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <unistd.h>
using socket_t = int;
#define A3MCP_BAD_SOCKET (-1)
// A write to a closed socket must not raise SIGPIPE: that would kill the game.
#define A3MCP_SEND_FLAGS MSG_NOSIGNAL
static void close_socket(socket_t s) { ::close(s); }
#endif

namespace a3mcp {

namespace {

constexpr size_t max_line = 64 * 1024 * 1024;

std::string trim(std::string_view s) {
    size_t b = 0, e = s.size();
    while (b < e && std::isspace(static_cast<unsigned char>(s[b]))) ++b;
    while (e > b && std::isspace(static_cast<unsigned char>(s[e - 1]))) --e;
    return std::string(s.substr(b, e - b));
}

bool truthy(const std::string& v) {
    std::string l = v;
    std::transform(l.begin(), l.end(), l.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return l == "1" || l == "true" || l == "yes" || l == "on";
}

void apply(config& cfg, const std::string& key, const std::string& value) {
    if (key == "host") {
        cfg.host = value;
    } else if (key == "port") {
        int p = std::atoi(value.c_str());
        if (p > 0 && p < 65536) cfg.port = p;
    } else if (key == "token") {
        cfg.token = value;
    } else if (key == "enabled") {
        cfg.enabled = truthy(value);
    } else if (key == "allow_client") {
        cfg.allow_client = truthy(value);
    }
}

std::string json_escape(std::string_view s) {
    std::string out = "\"";
    for (char c : s) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (static_cast<unsigned char>(c) < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", c);
                    out += buf;
                } else {
                    out += c;
                }
        }
    }
    return out + "\"";
}

// Splits "WORD rest" into WORD and rest.
std::pair<std::string_view, std::string_view> split_word(std::string_view s) {
    size_t sp = s.find(' ');
    if (sp == std::string_view::npos) return {s, {}};
    return {s.substr(0, sp), s.substr(sp + 1)};
}

}  // namespace

config load_config(const std::filesystem::path& dir) {
    config cfg;
    std::ifstream in(dir / "arma3_mcp.ini");
    std::string line;
    while (std::getline(in, line)) {
        size_t hash = line.find('#');
        if (hash != std::string::npos) line.resize(hash);
        size_t eq = line.find('=');
        if (eq == std::string::npos) continue;
        apply(cfg, trim(std::string_view(line).substr(0, eq)), trim(std::string_view(line).substr(eq + 1)));
    }
    const std::pair<const char*, const char*> env[] = {{"ARMA3_MCP_HOST", "host"},
                                                       {"ARMA3_MCP_PORT", "port"},
                                                       {"ARMA3_MCP_TOKEN", "token"},
                                                       {"ARMA3_MCP_ENABLED", "enabled"},
                                                       {"ARMA3_MCP_ALLOW_CLIENT", "allow_client"}};
    for (const auto& [var, key] : env) {
        const char* v = std::getenv(var);
        if (v && *v) apply(cfg, key, v);
    }
    return cfg;
}

std::string unquote_sqf(std::string_view s) {
    if (s.size() < 2 || s.front() != '"' || s.back() != '"') return std::string(s);
    s = s.substr(1, s.size() - 2);
    std::string out;
    out.reserve(s.size());
    for (size_t i = 0; i < s.size(); ++i) {
        out += s[i];
        if (s[i] == '"' && i + 1 < s.size() && s[i + 1] == '"') ++i;
    }
    return out;
}

size_t utf8_prefix(std::string_view s, size_t max) {
    if (s.size() <= max) return s.size();
    size_t n = max;
    // Step back over continuation bytes so the cut lands on the start of a character.
    while (n > 0 && (static_cast<unsigned char>(s[n]) & 0xC0) == 0x80) --n;
    return n == 0 ? max : n;
}

// ---------- connection ----------

class connection {
public:
    explicit connection(socket_t s) : socket_(s) {
        writer_ = std::thread([this] { write_loop(); });
    }
    ~connection() {
        close();
        if (writer_.joinable()) writer_.join();
        close_socket(socket_);
    }

    socket_t socket() const { return socket_; }
    bool authed = false;

    void send(std::string line) {
        line += '\n';
        std::lock_guard<std::mutex> lock(mutex_);
        if (closed_) return;
        out_.push_back(std::move(line));
        cv_.notify_one();
    }

    void close() {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (closed_) return;
            closed_ = true;
            cv_.notify_one();
        }
#ifdef _WIN32
        shutdown(socket_, SD_BOTH);
#else
        shutdown(socket_, SHUT_RDWR);
#endif
    }

private:
    // Sends queued lines on its own thread, so the game thread never blocks on a slow client.
    void write_loop() {
        for (;;) {
            std::string line;
            {
                std::unique_lock<std::mutex> lock(mutex_);
                cv_.wait(lock, [this] { return closed_ || !out_.empty(); });
                if (out_.empty()) return;
                line = std::move(out_.front());
                out_.pop_front();
            }
            size_t sent = 0;
            while (sent < line.size()) {
                int chunk = static_cast<int>(std::min<size_t>(line.size() - sent, 1 << 20));
                int n = ::send(socket_, line.data() + sent, chunk, A3MCP_SEND_FLAGS);
                if (n <= 0) {
                    close();
                    return;
                }
                sent += static_cast<size_t>(n);
            }
        }
    }

    socket_t socket_;
    std::mutex mutex_;
    std::condition_variable cv_;
    std::deque<std::string> out_;
    bool closed_ = false;
    std::thread writer_;
};

// ---------- bridge: network ----------

bool bridge::start(const config& cfg, std::string& message) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (listening_) {
        message = "listening on " + cfg_.host + ":" + std::to_string(cfg_.port);
        return true;
    }
#ifdef _WIN32
    static bool wsa = [] {
        WSADATA data;
        return WSAStartup(MAKEWORD(2, 2), &data) == 0;
    }();
    if (!wsa) {
        message = "WSAStartup failed";
        return false;
    }
#endif
    addrinfo hints{};
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    hints.ai_flags = AI_PASSIVE;
    addrinfo* res = nullptr;
    std::string port = std::to_string(cfg.port);
    if (getaddrinfo(cfg.host.c_str(), port.c_str(), &hints, &res) != 0 || !res) {
        message = "cannot resolve " + cfg.host;
        return false;
    }
    socket_t s = A3MCP_BAD_SOCKET;
    for (addrinfo* a = res; a; a = a->ai_next) {
        s = ::socket(a->ai_family, a->ai_socktype, a->ai_protocol);
        if (s == A3MCP_BAD_SOCKET) continue;
#ifndef _WIN32
        int yes = 1;
        setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
#endif
        if (::bind(s, a->ai_addr, static_cast<int>(a->ai_addrlen)) == 0 && ::listen(s, 8) == 0) break;
        close_socket(s);
        s = A3MCP_BAD_SOCKET;
    }
    freeaddrinfo(res);
    if (s == A3MCP_BAD_SOCKET) {
        message = "cannot listen on " + cfg.host + ":" + port + " (port in use?)";
        return false;
    }
    cfg_ = cfg;
    listen_socket_ = static_cast<std::intptr_t>(s);
    listening_ = true;
    std::thread([this] { accept_loop(); }).detach();
    message = "listening on " + cfg.host + ":" + port + (cfg.token.empty() ? "" : " (token required)");
    return true;
}

void bridge::accept_loop() {
    socket_t ls = static_cast<socket_t>(listen_socket_);
    for (;;) {
        socket_t s = ::accept(ls, nullptr, nullptr);
        if (s == A3MCP_BAD_SOCKET) {
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
            continue;
        }
        int yes = 1;
        setsockopt(s, IPPROTO_TCP, TCP_NODELAY, reinterpret_cast<const char*>(&yes), sizeof(yes));
        auto conn = std::make_shared<connection>(s);
        std::thread([this, conn] { serve(conn); }).detach();
    }
}

void bridge::serve(std::shared_ptr<connection> conn) {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        ++connections_;
        conn->authed = cfg_.token.empty();
    }
    std::string buffer;
    char chunk[64 * 1024];
    bool open = true;
    while (open) {
        int n = ::recv(conn->socket(), chunk, sizeof(chunk), 0);
        if (n <= 0) break;
        buffer.append(chunk, static_cast<size_t>(n));
        size_t start = 0;
        for (;;) {
            size_t nl = buffer.find('\n', start);
            if (nl == std::string::npos) break;
            std::string_view line(buffer.data() + start, nl - start);
            if (!line.empty() && line.back() == '\r') line.remove_suffix(1);
            if (!conn->authed) {
                auto [word, rest] = split_word(line);
                if (word == "AUTH" && rest == cfg_.token) {
                    conn->authed = true;
                    conn->send("OK auth");
                } else {
                    conn->send("ERR authentication required: send AUTH <token> first");
                    open = false;
                    break;
                }
            } else if (!line.empty()) {
                handle_line(conn, line);
            }
            start = nl + 1;
        }
        buffer.erase(0, start);
        if (buffer.size() > max_line) {
            conn->send("ERR line too long");
            break;
        }
    }
    conn->close();
    std::lock_guard<std::mutex> lock(mutex_);
    --connections_;
    // Requests still queued for this connection are dropped by poll() once the connection is gone.
}

void bridge::handle_line(const std::shared_ptr<connection>& conn, std::string_view line) {
    auto [cmd, rest] = split_word(line);
    if (cmd == "PING") {
        conn->send("PONG " + std::string(rest));
    } else if (cmd == "STATUS") {
        conn->send("STATUS " + std::string(rest) + " " + status_json());
    } else if (cmd == "EXEC") {
        auto [client_id, payload] = split_word(rest);
        if (client_id.empty() || payload.empty()) {
            conn->send("ERROR " + std::string(client_id) + " malformed EXEC");
            return;
        }
        auto req = std::make_shared<request>();
        req->client_id = std::string(client_id);
        req->conn = conn;
        req->payload = std::string(payload);
        {
            std::lock_guard<std::mutex> lock(mutex_);
            req->id = std::to_string(next_id_++);
            queue_.push_back(std::move(req));
        }
        wake();
    } else if (cmd == "CANCEL") {
        std::lock_guard<std::mutex> lock(mutex_);
        queue_.erase(std::remove_if(queue_.begin(), queue_.end(),
                                    [&](const std::shared_ptr<request>& r) {
                                        return r->client_id == rest && r->conn.lock() == conn;
                                    }),
                     queue_.end());
    } else {
        conn->send("ERR unknown command " + std::string(cmd));
    }
}

void bridge::set_wake(wake_fn fn) {
    std::lock_guard<std::mutex> lock(mutex_);
    wake_fn_ = std::move(fn);
}

void bridge::wake() {
    wake_fn fn;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (signaled_ || !wake_fn_) return;
        signaled_ = true;
        fn = wake_fn_;
    }
    if (!fn()) {
        // The engine's callback queue is full; the game's periodic poll will pick the request up.
        std::lock_guard<std::mutex> lock(mutex_);
        signaled_ = false;
    }
}

// ---------- bridge: game thread ----------

std::optional<std::pair<std::string, size_t>> bridge::poll() {
    std::lock_guard<std::mutex> lock(mutex_);
    last_poll_ = std::chrono::steady_clock::now();
    polled_ = true;
    while (!queue_.empty()) {
        auto req = std::move(queue_.front());
        queue_.pop_front();
        if (req->conn.expired()) continue;
        std::pair<std::string, size_t> head{req->id, req->payload.size()};
        active_[req->id] = std::move(req);
        return head;
    }
    signaled_ = false;
    return std::nullopt;
}

bool bridge::read(const std::string& id, size_t max, std::string& chunk, bool& more) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = active_.find(id);
    if (it == active_.end()) return false;
    request& req = *it->second;
    std::string_view rest = std::string_view(req.payload).substr(req.offset);
    size_t n = utf8_prefix(rest, max);
    chunk.assign(rest.substr(0, n));
    req.offset += n;
    more = req.offset < req.payload.size();
    return true;
}

bool bridge::append(const std::string& id, std::string_view chunk) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = active_.find(id);
    if (it == active_.end()) return false;
    it->second->response.append(chunk);
    return true;
}

bool bridge::finish(const std::string& id) {
    std::shared_ptr<request> req;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        auto it = active_.find(id);
        if (it == active_.end()) return false;
        req = std::move(it->second);
        active_.erase(it);
        ++served_;
    }
    if (auto conn = req->conn.lock()) {
        std::string& r = req->response;
        // The reply must stay on one line. Raw line breaks can only sit between JSON tokens
        // (inside strings they are escaped), so a space is equivalent there.
        std::replace(r.begin(), r.end(), '\n', ' ');
        std::replace(r.begin(), r.end(), '\r', ' ');
        conn->send("RESULT " + req->client_id + " " + (r.empty() ? std::string("null") : r));
    }
    return true;
}

void bridge::reset(const std::string& reason) {
    std::unordered_map<std::string, std::shared_ptr<request>> dropped;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        dropped.swap(active_);
    }
    for (auto& [id, req] : dropped) {
        if (auto conn = req->conn.lock()) conn->send("ERROR " + req->client_id + " " + reason);
    }
}

void bridge::set_game_info(std::string json) {
    std::lock_guard<std::mutex> lock(mutex_);
    game_info_ = std::move(json);
}

std::string bridge::status_json() {
    std::lock_guard<std::mutex> lock(mutex_);
    std::string poll_age = "null";
    if (polled_) {
        auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - last_poll_).count();
        poll_age = std::to_string(ms);
    }
    return std::string("{") + "\"version\":" + json_escape(A3MCP_VERSION) + ",\"listening\":" + (listening_ ? "true" : "false") +
           ",\"address\":" + json_escape(cfg_.host + ":" + std::to_string(cfg_.port)) +
           ",\"connections\":" + std::to_string(connections_) + ",\"queued\":" + std::to_string(queue_.size()) +
           ",\"running\":" + std::to_string(active_.size()) + ",\"served\":" + std::to_string(served_) +
           ",\"last_poll_ms_ago\":" + poll_age + ",\"callback\":" + (wake_fn_ ? "true" : "false") + ",\"game\":" + game_info_ +
           "}";
}

}  // namespace a3mcp
