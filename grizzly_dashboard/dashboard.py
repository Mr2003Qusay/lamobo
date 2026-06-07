import os
import json
import httpx
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

PORT = 8000
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

NETWORK_CONFIG_FILE = os.path.join(DIRECTORY, "network_config.json")

def load_network_config():
    if os.path.exists(NETWORK_CONFIG_FILE):
        try:
            with open(NETWORK_CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"allow_other_devices": False}

def save_network_config(config_data):
    try:
        with open(NETWORK_CONFIG_FILE, "w") as f:
            json.dump(config_data, f)
    except Exception:
        pass

# Persistent client for Jio API — keeps TCP/TLS connection alive for fast reuse
_jio_client = httpx.Client(
    timeout=7,
    http2=False,
    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
    headers={
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9,ar;q=0.8",
        "connection": "keep-alive",
        "cookie": "JioSessionID=f5350d23-cda3-4792-9643-6cf37501d274; ssjsid=f5350d23-cda3-4792-9643-6cf37501d274; SameSite=None; NSC_Q6_kjp_njdsptfswjdf_WT_443=ffffffff0985b1b345525d5f4f58455e445a4a4229a0; ADRUM_BTa=R:46|g:84f8bf7a-e6e2-4b4e-9175-d9a18aac1c43|n:customer1_a309c9d0-b5ef-4ff1-8978-610c0b29df8f",
        "referer": "https://www.jio.com/selfcare/paybill/mobility/",
        "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    }
)

class ProxyHandler(BaseHTTPRequestHandler):
    def is_loopback(self):
        ip = self.client_address[0]
        return ip in ("127.0.0.1", "::1", "localhost") or ip.endswith("127.0.0.1") or ip == "::ffff:127.0.0.1"

    def check_access(self):
        config_data = load_network_config()
        if not config_data.get("allow_other_devices", False) and not self.is_loopback():
            self.send_response(403)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            html = """
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Access Restricted - GrizzlySMS</title>
                <style>
                    body {
                        background-color: #0c0f17;
                        color: #f1f2f6;
                        font-family: 'Outfit', 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        height: 100vh;
                        margin: 0;
                        padding: 20px;
                        box-sizing: border-box;
                    }
                    .card {
                        background: rgba(255, 255, 255, 0.03);
                        border: 1px solid rgba(255, 255, 255, 0.08);
                        border-radius: 16px;
                        padding: 35px 30px;
                        max-width: 500px;
                        text-align: center;
                        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
                        backdrop-filter: blur(8px);
                    }
                    h1 {
                        color: #ff5252;
                        margin-top: 0;
                        font-size: 24px;
                        font-weight: 700;
                    }
                    p {
                        color: #a4b0be;
                        line-height: 1.6;
                        font-size: 16px;
                    }
                    .highlight {
                        color: #fa7900;
                        font-weight: bold;
                    }
                    .icon {
                        font-size: 60px;
                        margin-bottom: 15px;
                        animation: shake 2s infinite;
                    }
                    @keyframes shake {
                        0%, 100% { transform: rotate(0deg); }
                        10%, 30%, 50%, 70%, 90% { transform: rotate(-5deg); }
                        20%, 40%, 60%, 80% { transform: rotate(5deg); }
                    }
                </style>
            </head>
            <body>
                <div class="card">
                    <div class="icon">🔒</div>
                    <h1>Network Access Restricted</h1>
                    <p>Access from other devices on the network is currently disabled.</p>
                    <p>To connect from this device, please open the dashboard on the host computer (<span class="highlight">http://localhost:8000</span>) and enable the <span class="highlight">"Allow other devices"</span> setting in the top header.</p>
                </div>
            </body>
            </html>
            """
            self.wfile.write(html.encode("utf-8"))
            return False
        return True

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        # 1. Check network access restriction
        if not self.check_access():
            return

        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query = parse_qs(parsed_url.query)

        # --- Network Sharing Config API ---
        if path == "/api/network-status":
            config_data = load_network_config()
            import socket
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
            except Exception:
                try:
                    hostname = socket.gethostname()
                    local_ip = socket.gethostbyname(hostname)
                except Exception:
                    local_ip = "127.0.0.1"

            result = {
                "allow_other_devices": config_data.get("allow_other_devices", False),
                "local_ip": local_ip,
                "port": PORT
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode("utf-8"))
            return

        elif path == "/api/toggle-network":
            # Only allow loopback to change the settings for security!
            if not self.is_loopback():
                self.send_response(403)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Only loopback can change network sharing"}).encode("utf-8"))
                return

            enable_param = query.get("enable", ["false"])[0].lower() == "true"
            config_data = {"allow_other_devices": enable_param}
            save_network_config(config_data)

            import socket
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
            except Exception:
                try:
                    hostname = socket.gethostname()
                    local_ip = socket.gethostbyname(hostname)
                except Exception:
                    local_ip = "127.0.0.1"

            result = {
                "allow_other_devices": enable_param,
                "local_ip": local_ip,
                "port": PORT,
                "status": "restarting"
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode("utf-8"))

            import threading
            threading.Thread(target=trigger_restart, daemon=True).start()
            return

        # Static assets
        if path == "/" or path == "/index.html":
            self.serve_file("index.html", "text/html")
            return
        elif path == "/style.css":
            self.serve_file("style.css", "text/css")
            return
        elif path == "/app.js":
            self.serve_file("app.js", "application/javascript")
            return

        # --- GrizzlySMS Partner API ---
        if path == "/api/balance":
            api_key = query.get("api_key", [""])[0]
            url = f"https://api.grizzlysms.com/stubs/handler_api.php?api_key={api_key}&action=getBalance"
            self.forward_get(url)
            return
        elif path == "/api/active-rentals":
            api_key = query.get("api_key", [""])[0]
            url = f"https://api.grizzlysms.com/stubs/handler_api.php?api_key={api_key}&action=getActiveActivations"
            self.forward_get_json(url)
            return
        elif path == "/api/rent":
            api_key = query.get("api_key", [""])[0]
            service = query.get("service", [""])[0]
            country = query.get("country", [""])[0]
            max_price = query.get("maxPrice", [""])[0]
            url = f"https://api.grizzlysms.com/stubs/handler_api.php?api_key={api_key}&action=getNumberV2&service={service}&country={country}&maxPrice={max_price}"
            self.forward_get_json(url)
            return
        elif path == "/api/set-status":
            api_key = query.get("api_key", [""])[0]
            act_id = query.get("id", [""])[0]
            status = query.get("status", [""])[0]
            url = f"https://api.grizzlysms.com/stubs/handler_api.php?api_key={api_key}&action=setStatus&status={status}&id={act_id}"
            self.forward_get(url)
            return
        elif path == "/api/get-status":
            api_key = query.get("api_key", [""])[0]
            act_id = query.get("id", [""])[0]
            url = f"https://api.grizzlysms.com/stubs/handler_api.php?api_key={api_key}&action=getStatusV2&id={act_id}"
            self.forward_get_json(url)
            return

        # --- GrizzlySMS User API ---
        elif path == "/api/user-balance":
            token = query.get("token", [""])[0]
            session = query.get("session", [""])[0]
            headers = {"Authorization": f"Bearer {token}", "x-session-token": session, "user-agent": "Mozilla/5.0"}
            self.forward_user_api("https://grizzlysms.com/api/sms-users/balance", headers)
            return
        elif path == "/api/user-numbers":
            token = query.get("token", [""])[0]
            session = query.get("session", [""])[0]
            headers = {"Authorization": f"Bearer {token}", "x-session-token": session, "user-agent": "Mozilla/5.0"}
            self.forward_user_api("https://grizzlysms.com/api/sms-users/numbers", headers)
            return
        elif path == "/api/user-rent":
            token = query.get("token", [""])[0]
            session = query.get("session", [""])[0]
            country = query.get("country", [""])[0]
            service = query.get("service", [""])[0]
            max_price = query.get("maxPrice", [""])[0]
            headers = {"Authorization": f"Bearer {token}", "x-session-token": session, "user-agent": "Mozilla/5.0"}
            url = f"https://grizzlysms.com/api/sms-users/get-number/{country}/{service}?maxPrice={max_price}"
            self.forward_user_api(url, headers)
            return

        # --- PVACodes API ---
        elif path == "/api/pvacodes/balance":
            api_key = query.get("key", [""])[0]
            url = f"https://beta.pvacodes.com/app/api.php?do=check_balance&key={api_key}"
            self.forward_get_json(url)
            return
        elif path == "/api/pvacodes/countries":
            api_key = query.get("key", [""])[0]
            url = f"https://beta.pvacodes.com/app/api.php?do=get_countries&key={api_key}"
            self.forward_get_json(url)
            return
        elif path == "/api/pvacodes/apps":
            api_key = query.get("key", [""])[0]
            country = query.get("country", [""])[0]
            url = f"https://beta.pvacodes.com/app/api.php?do=get_apps&country={country}&key={api_key}"
            self.forward_get_json(url)
            return
        elif path == "/api/pvacodes/rent":
            api_key = query.get("key", [""])[0]
            country = query.get("country", [""])[0]
            app_name = query.get("app", ["Jio5"])[0]
            number = query.get("number", [""])[0]
            url = f"https://beta.pvacodes.com/app/api.php?do=get_number&country={country}&app={app_name}&key={api_key}"
            if number:
                url += f"&number={number}"
            self.forward_get_json(url)
            return
        elif path == "/api/pvacodes/refresh-sms":
            cookie = query.get("cookie", [""])[0]
            act_id = query.get("id", [""])[0]
            
            if not cookie or cookie in ("undefined", "null", "auto"):
                try:
                    config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "grizzly_config.json"))
                    if not os.path.exists(config_path):
                        config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "grizzly_config.json"))
                    if not os.path.exists(config_path):
                        config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "grizzly_config.json"))
                    if os.path.exists(config_path):
                        with open(config_path, "r") as f:
                            cfg = json.load(f)
                        cookie = cfg.get("api_keys", {}).get("pvacodesCookie", "")
                except Exception as ce:
                    logger.error(f"Failed to load pvacodesCookie from config in dashboard: {ce}")
                    
            url = "https://beta.pvacodes.com/app/index.php?page=user/app"
            headers = {
                "Cookie": cookie,
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
                "Referer": "https://beta.pvacodes.com/app/index?page=user/app"
            }
            payload = f"page=user%2Fapp&refresh_sms=1&id={act_id}"
            try:
                with httpx.Client(timeout=30) as client:
                    r = client.post(url, headers=headers, content=payload)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                try:
                    data = r.json()
                    self.wfile.write(json.dumps(data).encode("utf-8"))
                except Exception:
                    self.wfile.write(json.dumps({"raw_response": r.text}).encode("utf-8"))
            except Exception as e:
                self.send_response(502)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return
        elif path == "/api/pvacodes/sms":
            api_key = query.get("key", [""])[0]
            country = query.get("country", [""])[0]
            app = query.get("app", [""])[0]
            number = query.get("number", [""])[0]
            url = f"https://beta.pvacodes.com/app/api.php?do=get_sms&country={country}&app={app}&number={number}&key={api_key}"
            self.forward_get_json(url)
            return
        elif path == "/api/pvacodes/cancel":
            api_key = query.get("key", [""])[0]
            number_id = query.get("number_id", [""])[0]
            url = f"https://beta.pvacodes.com/app/api.php?do=cancel_number&number_id={number_id}&key={api_key}"
            self.forward_get_json(url)
            return
        elif path == "/api/pvacodes/history":
            api_key = query.get("key", [""])[0]
            country = query.get("country", [""])[0]
            app = query.get("app", [""])[0]
            base_url = f"https://beta.pvacodes.com/app/api.php?do=get_history&key={api_key}"
            if country:
                base_url += f"&country={country}"
            if app:
                base_url += f"&app={app}"
            self.forward_get_json(base_url)
            return

        # --- OTP Doctor API ---
        elif path == "/api/otpdoctor/balance":
            api_key = query.get("api_key", [""])[0]
            url = f"https://www.otpdoctor.in/stubs/handler_api.php?action=getBalance&api_key={api_key}"
            self.forward_get(url)
            return
        elif path == "/api/otpdoctor/services":
            api_key = query.get("api_key", [""])[0]
            country = query.get("country", [""])[0]
            url = f"https://www.otpdoctor.in/stubs/handler_api.php?action=getServices&api_key={api_key}&country={country}"
            self.forward_get_json(url)
            return
        elif path == "/api/otpdoctor/rent":
            api_key = query.get("api_key", [""])[0]
            service = query.get("service", [""])[0]
            max_price = query.get("maxPrice", [""])[0]
            country = query.get("country", [""])[0]
            url = f"https://www.otpdoctor.in/stubs/handler_api.php?action=getNumber&api_key={api_key}&service={service}"
            if max_price:
                url += f"&maxPrice={max_price}"
            if country:
                url += f"&country={country}"
            self.forward_get(url)
            return
        elif path == "/api/otpdoctor/get-status":
            api_key = query.get("api_key", [""])[0]
            act_id = query.get("id", [""])[0]
            url = f"https://www.otpdoctor.in/stubs/handler_api.php?action=getStatus&api_key={api_key}&id={act_id}"
            self.forward_get(url)
            return
        elif path == "/api/otpdoctor/set-status":
            api_key = query.get("api_key", [""])[0]
            act_id = query.get("id", [""])[0]
            status = query.get("status", [""])[0]
            url = f"https://www.otpdoctor.in/stubs/handler_api.php?action=setStatus&api_key={api_key}&id={act_id}&status={status}"
            self.forward_get(url)
            return

        # --- Jio Number Testing API ---
        elif path == "/api/check-number":
            phone = query.get("phone", [""])[0]
            if not phone:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing phone number")
                return

            url = f"https://www.jio.com/api/jio-recharge-service/recharge/mobility/number/{phone}"

            try:
                r = _jio_client.get(url)

                try:
                    data = r.json()
                    if "primaryService" in data and data["primaryService"].get("serviceId") == phone:
                        result = {"valid": True, "details": data}
                    else:
                        result = {"valid": False, "details": data}
                except Exception:
                    result = {"valid": False, "raw_response": r.text}

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(result).encode("utf-8"))
            except Exception as e:
                self.send_response(502)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"valid": False, "error": str(e)}).encode("utf-8"))
            return

        self.send_error(404, "Not Found")

    def do_POST(self):
        if not self.check_access():
            return
        self.send_error(404, "Not Found")

    def serve_file(self, filename, content_type):
        filepath = os.path.join(DIRECTORY, filename)
        if not os.path.exists(filepath):
            self.send_error(404, "File Not Found")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        with open(filepath, "rb") as f:
            self.wfile.write(f.read())

    def forward_get(self, url):
        try:
            with httpx.Client(timeout=15) as client:
                r = client.get(url)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(r.text.encode("utf-8"))
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(f"Proxy error: {e}".encode("utf-8"))

    def forward_get_json(self, url):
        try:
            with httpx.Client(timeout=15) as client:
                r = client.get(url)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                data = r.json()
                self.wfile.write(json.dumps(data).encode("utf-8"))
            except Exception:
                # Try to extract valid JSON from response that may have trailing garbage
                text = r.text.strip()
                data = self._extract_json(text)
                if data is not None:
                    self.wfile.write(json.dumps(data).encode("utf-8"))
                else:
                    self.wfile.write(json.dumps({"raw_response": text}).encode("utf-8"))
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))

    def _extract_json(self, text):
        """Try to extract the first valid JSON object/array from text with trailing garbage."""
        if not text:
            return None
        # Find the opening bracket
        start_char = None
        end_char = None
        start_idx = -1
        for i, c in enumerate(text):
            if c == '{':
                start_char = '{'
                end_char = '}'
                start_idx = i
                break
            elif c == '[':
                start_char = '['
                end_char = ']'
                start_idx = i
                break
        if start_idx == -1:
            return None
        # Walk through and find matching close
        depth = 0
        in_string = False
        escape = False
        for i in range(start_idx, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == '\\' and in_string:
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == start_char:
                depth += 1
            elif c == end_char:
                depth -= 1
                if depth == 0:
                    candidate = text[start_idx:i+1]
                    try:
                        return json.loads(candidate)
                    except Exception:
                        return None
        return None

    def forward_user_api(self, url, headers):
        try:
            with httpx.Client(timeout=15) as client:
                r = client.get(url, headers=headers)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                data = r.json()
                self.wfile.write(json.dumps(data).encode("utf-8"))
            except Exception:
                self.wfile.write(json.dumps({"value": r.text.strip()}).encode("utf-8"))
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle each request in a new thread for concurrent processing."""
    daemon_threads = True

server_instance = None
server_running = True

def trigger_restart():
    global server_instance
    import time
    time.sleep(0.5)
    if server_instance:
        try:
            print("Stopping current HTTP server...")
            server_instance.shutdown()
            server_instance.server_close()
            print("HTTP server stopped cleanly.")
        except Exception as e:
            print(f"Error shutting down server: {e}")

def run():
    global server_running, server_instance
    import socket
    
    while server_running:
        config_data = load_network_config()
        allow_other = config_data.get("allow_other_devices", False)
        bind_ip = "0.0.0.0" if allow_other else "127.0.0.1"

        hostname = socket.gethostname()
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            try:
                local_ip = socket.gethostbyname(hostname)
            except Exception:
                local_ip = "127.0.0.1"

        print(f"\nStarting GrizzlySMS Dashboard (threaded) on:")
        print(f"  Local:   http://localhost:{PORT}")
        if allow_other:
            print(f"  Network: http://{local_ip}:{PORT}")
        else:
            print(f"  Network: DISABLED (Strict Localhost Only)")

        server_instance = None
        for attempt in range(12):
            try:
                server_instance = ThreadedHTTPServer((bind_ip, PORT), ProxyHandler)
                break
            except OSError as e:
                if attempt < 11:
                    print(f"Port {PORT} is busy, retrying in 0.5s (attempt {attempt+1}/12)...")
                    import time
                    time.sleep(0.5)
                else:
                    print(f"Failed to bind to port {PORT} after 12 attempts: {e}")
                    server_running = False
                    return

        try:
            server_instance.serve_forever()
        except KeyboardInterrupt:
            print("\nStopping server...")
            server_instance.server_close()
            server_running = False
            break
        except Exception as e:
            print(f"Server exception: {e}")
            server_running = False
            break

if __name__ == "__main__":
    run()