import "@fontsource/fira-sans-condensed/400.css";
import "@fontsource/fira-sans-condensed/500.css";
import "@fontsource/fira-sans-condensed/600.css";
import "@fontsource/fira-sans-condensed/700.css";
import "./styles.css";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { SendPage } from "./send/SendPage";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false } },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      {window.location.pathname.startsWith("/send") ? <SendPage /> : <App />}
    </QueryClientProvider>
  </StrictMode>,
);
