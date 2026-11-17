import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { createApi } from "./lib/api";
import { takeToken } from "./lib/token";
import "./styles.css";

const token = takeToken(window);
const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App api={token ? createApi(token) : null} />
    </StrictMode>,
  );
}
