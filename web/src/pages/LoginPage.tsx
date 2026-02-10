import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { API_BASE_URL } from "../api";

export function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const navigate = useNavigate();

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    try {
      const formBody = new URLSearchParams();
      formBody.append("username", username);
      formBody.append("password", password);
      const response = await fetch(`${API_BASE_URL}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: formBody.toString(),
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      const data = await response.json();
      localStorage.setItem("token", data.access_token);
      navigate("/labs");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Login failed");
    }
  }

  return (
    <div className="auth-card">
      <div className="auth-title">
        <h2>Sign in</h2>
        <p>Use your account to manage labs and start devices.</p>
      </div>
      <form onSubmit={handleSubmit} className="auth-form">
        <label>
          Username
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="username"
          />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
          />
        </label>
        <button type="submit">Sign in</button>
      </form>
      {status && <p className="auth-status">{status}</p>}
    </div>
  );
}
