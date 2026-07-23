import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { isPublicSharePath, publicGameIdFromPath } from "./lab/lab-routes"

import "./index.css"

const root = createRoot(document.getElementById("root")!)
const isPublicSite = import.meta.env.MODE === "pages"

async function bootstrap() {
  if (isPublicSite) {
    const { MarketingSite } = await import("./marketing/marketing-site")
    document.title = "WorldEval — Evaluate intelligent agents in worlds"
    root.render(
      <StrictMode>
        <MarketingSite />
      </StrictMode>
    )
    return
  }

  const [{ LabApp, PublicLabApp, PublicShareLanding }, { ThemeProvider }] =
    await Promise.all([
      import("./lab/LabApp.tsx"),
      import("@/components/theme-provider.tsx"),
    ])
  const publicGameId = publicGameIdFromPath()
  const publicShare = isPublicSharePath()
  document.title = publicGameId
    ? "WorldEval — Game guide"
    : publicShare
      ? "WorldEval — Public game guides"
      : "WorldEval Lab"
  root.render(
    <StrictMode>
      <ThemeProvider defaultTheme="dark">
        {publicGameId ? (
          <PublicLabApp gameId={publicGameId} />
        ) : publicShare ? (
          <PublicShareLanding />
        ) : (
          <LabApp />
        )}
      </ThemeProvider>
    </StrictMode>
  )
}

void bootstrap()
