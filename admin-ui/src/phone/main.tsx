import React from "react";
import { createRoot } from "react-dom/client";
import { PhoneApp } from "./PhoneApp";

createRoot(document.getElementById("phone-root")!).render(
  <React.StrictMode>
    <PhoneApp />
  </React.StrictMode>,
);
