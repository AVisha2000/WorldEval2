import {AbsoluteFill, Easing, interpolate, useCurrentFrame} from 'remotion';

const FPS = 30;
export const WORLD_EVAL_CODEX_OUTRO_DURATION = 30 * FPS;

const colors = {
  ink: '#071018',
  text: '#F8FBF7',
  muted: '#B6C5CC',
  cyan: '#54D9E8',
  mint: '#65E6BE',
  amber: '#FFC35C',
  purple: '#B9A1FF',
  line: 'rgba(190, 228, 235, 0.24)',
};

const ease = Easing.bezier(0.16, 1, 0.3, 1);

const enter = (frame: number, delay: number, duration = 24) =>
  interpolate(frame, [delay, delay + duration], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: ease,
  });

const lift = (frame: number, delay: number, distance = 22) =>
  interpolate(frame, [delay, delay + 24], [distance, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: ease,
  });

const Brand = () => (
  <div
    style={{
      position: 'absolute', top: 42, left: 64, right: 64, display: 'flex',
      justifyContent: 'space-between', alignItems: 'center', color: colors.text,
      fontFamily: 'Arial, Helvetica, sans-serif', fontSize: 20, fontWeight: 800,
      letterSpacing: 1.8,
    }}
  >
    <div style={{display: 'flex', gap: 18, alignItems: 'center'}}>
      <span>WORLDEVAL</span>
      <span style={{height: 22, width: 1, background: colors.line}} />
      <span style={{color: colors.muted}}>BUILT WITH CODEX</span>
    </div>
    <div style={{color: colors.cyan}}>05 / 05</div>
  </div>
);

export const WorldEvalCodexOutro = () => {
  const frame = useCurrentFrame();
  const stages = [
    ['VOICE', colors.cyan],
    ['PLAN', colors.amber],
    ['BUILD', colors.purple],
    ['ITERATE', colors.mint],
    ['DEMO', colors.text],
  ] as const;

  return (
    <AbsoluteFill style={{background: colors.ink, color: colors.text, fontFamily: 'Arial, Helvetica, sans-serif', overflow: 'hidden'}}>
      <AbsoluteFill style={{background: 'radial-gradient(circle at 78% 18%, rgba(84,217,232,0.15), transparent 32%), radial-gradient(circle at 13% 92%, rgba(255,195,92,0.13), transparent 27%)'}} />
      <div style={{position: 'absolute', inset: 0, backgroundImage: 'linear-gradient(rgba(84,217,232,0.035) 1px, transparent 1px), linear-gradient(90deg, rgba(84,217,232,0.035) 1px, transparent 1px)', backgroundSize: '48px 48px'}} />
      <Brand />

      <div style={{position: 'absolute', left: 86, right: 86, top: 180}}>
        <div style={{color: colors.amber, fontSize: 20, fontWeight: 900, letterSpacing: 4.8, opacity: enter(frame, 18)}}>THIS PROJECT WAS PROMPTED INTO EXISTENCE</div>
        <div style={{marginTop: 22, fontSize: 82, lineHeight: 1, fontWeight: 900, letterSpacing: -3.8, opacity: enter(frame, 34), translate: `0 ${lift(frame, 34, 30)}px`}}>
          Built end-to-end with
          <br />
          <span style={{color: colors.cyan}}>Codex + GPT-5.6.</span>
        </div>
        <div style={{display: 'flex', alignItems: 'center', gap: 16, marginTop: 54}}>
          {stages.map(([label, color], index) => (
            <div key={label} style={{display: 'flex', alignItems: 'center', gap: 16, opacity: enter(frame, 98 + index * 25), translate: `0 ${lift(frame, 98 + index * 25, 18)}px`}}>
              <div style={{minWidth: label === 'ITERATE' ? 153 : 116, padding: '17px 20px', borderRadius: 16, border: `1px solid ${color}88`, background: 'rgba(4, 12, 19, 0.9)', color, textAlign: 'center', fontSize: 20, fontWeight: 900, letterSpacing: 1.6}}>{label}</div>
              {index < stages.length - 1 && <div style={{color: colors.muted, fontSize: 32, fontWeight: 700}}>→</div>}
            </div>
          ))}
        </div>
      </div>

      <div style={{position: 'absolute', left: 64, right: 64, bottom: 50, height: 2, background: colors.line}} />
      <div style={{position: 'absolute', left: 86, bottom: 116, opacity: enter(frame, 258), translate: `0 ${lift(frame, 258, 24)}px`}}>
        <div style={{color: colors.mint, fontSize: 20, fontWeight: 900, letterSpacing: 4.6}}>WORLDEVAL</div>
        <div style={{marginTop: 17, fontSize: 46, fontWeight: 800, letterSpacing: -1.4}}>
          Evaluate agents where actions have consequences.
        </div>
      </div>
    </AbsoluteFill>
  );
};
