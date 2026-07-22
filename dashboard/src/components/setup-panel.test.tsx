import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it, vi } from "vitest"
import type { EpisodeSetup } from "@/api"
import { SetupPanel } from "@/components/setup-panel"

const setup: EpisodeSetup = {
  controllerMode: "live_provider",
  mode: "solo",
  provider: "openai",
  model: "gpt-5.6-sol",
  apiKey: "",
  opponentProvider: "scripted",
  opponentModel: "balanced-v1",
  opponentApiKey: "",
  scenarioId: "construction-v0",
  taskId: "construction-v0",
  seed: 20240520,
}

describe("SetupPanel", () => {
  afterEach(cleanup)

  it("offers the OpenAI 5.6 family at fixed low reasoning effort", async () => {
    const user = userEvent.setup()
    render(<SetupPanel setup={setup} pending={false} onChange={vi.fn()} onSubmit={vi.fn()} onQuickStart={vi.fn()} onTeamQuickStart={vi.fn()} onRtsQuickStart={vi.fn()} onMazeQuickStart={vi.fn()} onCrossroadsQuickStart={vi.fn()} onSoloQuickStart={vi.fn()} />)

    await user.click(screen.getByRole("combobox", { name: "Model" }))
    expect(await screen.findByRole("option", { name: "GPT-5.6 Sol · flagship" })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "GPT-5.6 Terra · balanced" })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "GPT-5.6 Luna · fast" })).toBeInTheDocument()
    expect(screen.getByText("OpenAI Responses · reasoning effort fixed to low.")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Play Labyrinth Run" })).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Configurable deterministic demos")).not.toBeInTheDocument()
  })

  it("configures live Labyrinth sightlines without changing other game modes", async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<SetupPanel setup={{ ...setup, mode: "trio", mazeVisionRange: 4 }} pending={false} onChange={onChange} onSubmit={vi.fn()} onQuickStart={vi.fn()} onTeamQuickStart={vi.fn()} onRtsQuickStart={vi.fn()} onMazeQuickStart={vi.fn()} onCrossroadsQuickStart={vi.fn()} onSoloQuickStart={vi.fn()} />)

    await user.click(screen.getByRole("combobox", { name: "Agent field of view" }))
    expect(await screen.findByRole("option", { name: "1 cell · immediate surroundings" })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "Infinite · to the next wall" })).toBeInTheDocument()
    await user.click(screen.getByRole("option", { name: "Infinite · to the next wall" }))
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ mazeVisionRange: "infinite" }))
    expect(screen.getByText(/explicitly issue a bounded follow-corridor command/i)).toBeInTheDocument()
  })

  it("starts any scripted solo stage without a key and hides provider controls", async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    const onSubmit = vi.fn()
    render(<SetupPanel setup={{ ...setup, controllerMode: "scripted_demo", scenarioId: "orientation-v0" }} pending={false} onChange={onChange} onSubmit={onSubmit} onQuickStart={vi.fn()} onTeamQuickStart={vi.fn()} onRtsQuickStart={vi.fn()} onMazeQuickStart={vi.fn()} onCrossroadsQuickStart={vi.fn()} onSoloQuickStart={vi.fn()} />)

    expect(screen.getByRole("button", { name: "Run selected solo demo" })).toBeEnabled()
    expect(screen.queryByLabelText("API key")).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Provider")).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Model")).not.toBeInTheDocument()
    expect(screen.getByRole("combobox", { name: "Selected demo folder" })).toBeEnabled()
    expect(screen.getByText(/Saved highlights and local deterministic fixtures/i)).toBeInTheDocument()

    await user.click(screen.getByRole("combobox", { name: "Demo scenario" }))
    expect(await screen.findByRole("option", { name: "Stage A Orientation" })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "Stage B Interaction" })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "Stage C Construction" })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "Stage D Neutral Encounter" })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "Multi-action solo showcase" })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "Movement maze" })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "Operator action course" })).toBeInTheDocument()
    await user.click(screen.getByRole("option", { name: "Multi-action solo showcase" }))
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ scenarioId: "multi-action-demo-v0" }))

    await user.click(screen.getByRole("radio", { name: "Live games" }))
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ controllerMode: "live_provider" }))
  })

  it("offers a keyless Alpha versus Bravo two-leg Demo workflow", async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    const duel = { ...setup, controllerMode: "scripted_demo" as const, mode: "duel" as const }
    render(<SetupPanel setup={duel} pending={false} onChange={onChange} onSubmit={vi.fn()} onQuickStart={vi.fn()} onTeamQuickStart={vi.fn()} onRtsQuickStart={vi.fn()} onMazeQuickStart={vi.fn()} onCrossroadsQuickStart={vi.fn()} onSoloQuickStart={vi.fn()} />)

    expect(screen.queryByLabelText("API key")).not.toBeInTheDocument()
    expect(screen.getByLabelText("Demo duel entrants")).toHaveTextContent("duelist-alpha-v1")
    expect(screen.getByLabelText("Demo duel entrants")).toHaveTextContent("duelist-bravo-v1")
    expect(screen.getByLabelText("Demo duel task")).toHaveValue("Central Relay · relay hold or knockout")
    expect(screen.getByRole("button", { name: "Run selected duo demo" })).toBeEnabled()

    await user.click(screen.getByRole("combobox", { name: "Selected demo folder" }))
    await user.click(await screen.findByRole("option", { name: "/demos/solo" }))
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ mode: "solo" }))
  })

  it("makes RTS Skirmish the primary credential-free MVP quick start", async () => {
    const user = userEvent.setup()
    const onRtsQuickStart = vi.fn()
    render(<SetupPanel setup={{ ...setup, controllerMode: "scripted_demo", mode: "duel", duoTaskId: "rts-skirmish-v0" }} pending={false} onChange={vi.fn()} onSubmit={vi.fn()} onQuickStart={vi.fn()} onTeamQuickStart={vi.fn()} onRtsQuickStart={onRtsQuickStart} onMazeQuickStart={vi.fn()} onCrossroadsQuickStart={vi.fn()} onSoloQuickStart={vi.fn()} />)

    expect(screen.getByText(/Terra and Luna Demo agents gather, build, arm, and fight/i)).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Run RTS Skirmish" }))
    expect(onRtsQuickStart).toHaveBeenCalledOnce()
  })

  it("highlights Labyrinth Run first with the three model colours", async () => {
    const user = userEvent.setup()
    const onMazeQuickStart = vi.fn()
    render(<SetupPanel setup={{ ...setup, controllerMode: "scripted_demo" }} pending={false} onChange={vi.fn()} onSubmit={vi.fn()} onQuickStart={vi.fn()} onTeamQuickStart={vi.fn()} onRtsQuickStart={vi.fn()} onMazeQuickStart={onMazeQuickStart} onCrossroadsQuickStart={vi.fn()} onSoloQuickStart={vi.fn()} />)

    expect(screen.getByRole("radio", { name: "Pre-run saves" })).toBeChecked()
    expect(screen.getByLabelText("Labyrinth Run models")).toHaveTextContent("Soldemo-sol-v1")
    expect(screen.getByLabelText("Labyrinth Run models")).toHaveTextContent("Terrademo-terra-v1")
    expect(screen.getByLabelText("Labyrinth Run models")).toHaveTextContent("Lunademo-luna-v1")
    await user.click(screen.getByRole("button", { name: "Play Labyrinth Run" }))
    expect(onMazeQuickStart).toHaveBeenCalledOnce()
  })

  it("offers Crossroads Conquest as the third cached quick-start", async () => {
    const user = userEvent.setup()
    const onCrossroadsQuickStart = vi.fn()
    render(<SetupPanel setup={{ ...setup, controllerMode: "scripted_demo" }} pending={false} onChange={vi.fn()} onSubmit={vi.fn()} onQuickStart={vi.fn()} onTeamQuickStart={vi.fn()} onRtsQuickStart={vi.fn()} onMazeQuickStart={vi.fn()} onCrossroadsQuickStart={onCrossroadsQuickStart} onSoloQuickStart={vi.fn()} />)

    const buttons = screen.getAllByRole("button", { name: /Play Labyrinth Run|Run RTS Skirmish|Run Crossroads Conquest/ })
    expect(buttons.map((button) => button.textContent)).toEqual([
      "Play Labyrinth Run",
      "Run RTS Skirmish",
      "Run Crossroads Conquest",
    ])
    expect(screen.getByLabelText("Crossroads Conquest factions")).toHaveTextContent("△ Sol")
    expect(screen.getByLabelText("Crossroads Conquest factions")).toHaveTextContent("○ Luna")
    expect(screen.getByLabelText("Crossroads Conquest factions")).toHaveTextContent("□ Terra")
    await user.click(screen.getByRole("button", { name: "Run Crossroads Conquest" }))
    expect(onCrossroadsQuickStart).toHaveBeenCalledOnce()
  })

  it("offers exactly Sol, Luna, and Terra across both three-leg Demo games", async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<SetupPanel setup={{
      ...setup,
      controllerMode: "scripted_demo",
      mode: "trio",
      trioTaskId: "trio-relay-v0",
    }} pending={false} onChange={onChange} onSubmit={vi.fn()} onQuickStart={vi.fn()} onTeamQuickStart={vi.fn()} onRtsQuickStart={vi.fn()} onMazeQuickStart={vi.fn()} onCrossroadsQuickStart={vi.fn()} onSoloQuickStart={vi.fn()} />)

    expect(screen.queryByLabelText("API key")).not.toBeInTheDocument()
    expect(screen.getByLabelText("Demo trio entrants")).toHaveTextContent("Sol · demo-sol-v1")
    expect(screen.getByLabelText("Demo trio entrants")).toHaveTextContent("Luna · demo-luna-v1")
    expect(screen.getByLabelText("Demo trio entrants")).toHaveTextContent("Terra · demo-terra-v1")
    expect(screen.getByLabelText("Demo trio timing")).toHaveValue(
      "3 cyclic legs · fixed 10-tick joint windows"
    )
    expect(screen.getByRole("button", { name: "Run selected trio demo" })).toBeEnabled()

    await user.click(screen.getByRole("combobox", { name: "Trio game" }))
    await user.click(await screen.findByRole("option", { name: "Trio Free-for-All" }))
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      trioTaskId: "trio-free-for-all-v0",
    }))
  })
})
