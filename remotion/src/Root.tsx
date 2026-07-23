import {Composition} from 'remotion';
import {
  WorldArenaIntro,
  type WorldArenaIntroProps,
} from './WorldArenaIntro';
import {WorldArenaExplainer} from './WorldArenaExplainer';
import {WorldEvalHackathonDemo} from './WorldEvalHackathonDemo';
import {
  WORLD_EVAL_HACKATHON_OPENER_DURATION,
  WorldEvalHackathonOpener,
} from './WorldEvalHackathonOpener';
import {
  WORLD_EVAL_CODEX_OUTRO_DURATION,
  WorldEvalCodexOutro,
} from './WorldEvalCodexOutro';

const defaultIntroProps: WorldArenaIntroProps = {
  title: 'WORLD ARENA',
  subtitle: 'Three minds. One world. Every decision has consequences.',
  roundCount: 40,
};

export const RemotionRoot = () => {
  return (
    <>
    <Composition
      id="WorldArenaIntro"
      component={WorldArenaIntro}
      durationInFrames={240}
      fps={30}
      width={1920}
      height={1080}
      defaultProps={defaultIntroProps}
    />
    <Composition
      id="WorldArenaExplainer"
      component={WorldArenaExplainer}
      durationInFrames={3600}
      fps={30}
      width={1920}
      height={1080}
    />
    <Composition
      id="WorldEvalHackathonDemo"
      component={WorldEvalHackathonDemo}
      durationInFrames={5400}
      fps={30}
      width={1920}
      height={1080}
    />
    <Composition
      id="WorldEvalHackathonOpener"
      component={WorldEvalHackathonOpener}
      durationInFrames={WORLD_EVAL_HACKATHON_OPENER_DURATION}
      fps={30}
      width={1920}
      height={1080}
    />
    <Composition
      id="WorldEvalCodexOutro"
      component={WorldEvalCodexOutro}
      durationInFrames={WORLD_EVAL_CODEX_OUTRO_DURATION}
      fps={30}
      width={1920}
      height={1080}
    />
    </>
  );
};
