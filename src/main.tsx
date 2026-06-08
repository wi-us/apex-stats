import { StrictMode, startTransition } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "@tanstack/react-router";

import { getDevRouter } from "./dev-router";
import "./styles.css";

const container = document.getElementById("root");

if (!container) {
  throw new Error("Root element #root was not found.");
}

const router = getDevRouter();

startTransition(() => {
  createRoot(container).render(
    <StrictMode>
      <RouterProvider router={router} />
    </StrictMode>,
  );
});
