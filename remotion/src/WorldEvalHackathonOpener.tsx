import {
  AbsoluteFill,
  Easing,
  Img,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
} from 'remotion';

const FPS = 30;

const colors = {
  ink: '#071018',
  panel: 'rgba(5, 13, 20, 0.9)',
  panelStrong: 'rgba(4, 10, 16, 0.97)',
  text: '#F8FBF7',
  muted: '#B6C5CC',
  cyan: '#54D9E8',
  mint: '#65E6BE',
  amber: '#FFC35C',
  coral: '#FF8278',
  purple: '#B9A1FF',
  line: 'rgba(190, 228, 235, 0.24)',
};

const ease = Easing.bezier(0.16, 1, 0.3, 1);

const reveal = (frame: number, delay = 0, duration = 22) =>
  interpolate(frame, [delay, delay + duration], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: ease,
  });

const rise = (frame: number, delay = 0, distance = 32) =>
  interpolate(frame, [delay, delay + 24], [distance, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: ease,
  });

const Brand = ({section, slide}: {section: string; slide: string}) => (
  <>
    <div
      style={{
        position: 'absolute',
        zIndex: 50,
        top: 42,
        left: 64,
        right: 64,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        color: colors.text,
        fontFamily: 'Arial, Helvetica, sans-serif',
        fontSize: 20,
        fontWeight: 800,
        letterSpacing: 1.8,
        textShadow: '0 4px 24px rgba(0,0,0,0.72)',
      }}
    >
      <div style={{display: 'flex', gap: 18, alignItems: 'center'}}>
        <span>WORLDEVAL</span>
        <span style={{height: 22, width: 1, background: colors.line}} />
        <span style={{color: colors.muted, fontWeight: 650}}>{section}</span>
      </div>
      <div style={{display: 'flex', gap: 20, alignItems: 'center'}}>
        <span style={{color: colors.muted}}>OPENAI BUILD WEEK</span>
        <span style={{color: colors.cyan}}>{slide}</span>
      </div>
    </div>
    <div
      style={{
        position: 'absolute',
        zIndex: 50,
        left: 64,
        right: 64,
        bottom: 34,
        height: 2,
        borderRadius: 999,
        background: 'rgba(255,255,255,0.18)',
      }}
    />
  </>
);

const WipeIn = ({accent}: {accent: string}) => {
  const frame = useCurrentFrame();
  return (
    <div
      style={{
        position: 'absolute',
        zIndex: 90,
        inset: 0,
        background: accent,
        translate: `${interpolate(frame, [0, 22], [0, -104], {
          extrapolateRight: 'clamp',
          easing: ease,
        })}% 0`,
      }}
    />
  );
};

const Vignette = ({strength = 0.78}: {strength?: number}) => (
  <AbsoluteFill
    style={{
      background: `radial-gradient(circle at 50% 42%, transparent 24%, rgba(1, 6, 10, ${strength}) 100%)`,
    }}
  />
);

const Arrow = ({frame, delay, color = colors.muted}: {frame: number; delay: number; color?: string}) => (
  <div
    style={{
      alignSelf: 'center',
      color,
      fontSize: 40,
      fontWeight: 700,
      opacity: reveal(frame, delay),
    }}
  >
    →
  </div>
);

const AgentRunVisual = () => {
  const frame = useCurrentFrame();
  const pathProgress = reveal(frame, 110, 86);
  const robotX = interpolate(pathProgress, [0, 1], [0, 428]);
  const robotY = interpolate(pathProgress, [0, 0.48, 1], [0, -56, 8]);
  const bubbleOpacity = reveal(frame, 50);

  return (
    <div
      style={{
        position: 'relative',
        height: 370,
        borderRadius: 28,
        overflow: 'hidden',
        border: `1px solid ${colors.cyan}55`,
        background: 'linear-gradient(135deg, rgba(7,28,36,0.98), rgba(4,14,20,0.96))',
        boxShadow: '0 24px 80px rgba(0,0,0,0.34)',
        opacity: reveal(frame, 48),
        translate: `0 ${rise(frame, 48, 26)}px`,
      }}
    >
      <div style={{position: 'absolute', inset: 0, backgroundImage: 'linear-gradient(rgba(84,217,232,0.06) 1px, transparent 1px), linear-gradient(90deg, rgba(84,217,232,0.06) 1px, transparent 1px)', backgroundSize: '36px 36px'}} />
      <div style={{position: 'absolute', top: 25, left: 30, color: colors.cyan, fontSize: 16, fontWeight: 900, letterSpacing: 2.4}}>ONE OBSERVABLE RUN</div>
      <div style={{position: 'absolute', top: 69, left: 31, color: colors.muted, fontSize: 18, fontWeight: 700}}>Observation</div>
      <div style={{position: 'absolute', top: 96, left: 31, width: 194, borderRadius: 15, padding: '14px 15px', background: 'rgba(2,8,12,0.72)', border: `1px solid ${colors.cyan}55`, color: colors.text, fontSize: 17, lineHeight: 1.35}}>
        Exit is north-east. Bridge is clear.
      </div>
      <div style={{position: 'absolute', left: 216, top: 141, opacity: bubbleOpacity, translate: `0 ${rise(frame, 50, 18)}px`}}>
        <div style={{position: 'relative', borderRadius: 15, padding: '13px 17px', background: colors.amber, color: colors.ink, fontSize: 18, fontWeight: 900, boxShadow: `0 12px 34px ${colors.amber}33`}}>
          Move toward the exit.
          <div style={{position: 'absolute', bottom: -9, left: 30, width: 18, height: 18, background: colors.amber, rotate: '45deg'}} />
        </div>
      </div>
      <div style={{position: 'absolute', left: 250, right: 56, bottom: 99, height: 3, background: 'rgba(101,230,190,0.2)', borderRadius: 99}}>
        <div style={{height: '100%', width: `${pathProgress * 100}%`, background: colors.mint, borderRadius: 99, boxShadow: `0 0 20px ${colors.mint}`}} />
      </div>
      <div style={{position: 'absolute', left: 250 + robotX, bottom: 74 - robotY, width: 50, height: 50, borderRadius: 16, background: colors.ink, border: `3px solid ${colors.amber}`, boxShadow: `0 0 25px ${colors.amber}66`}}>
        <div style={{position: 'absolute', left: 10, top: 16, width: 8, height: 8, borderRadius: '50%', background: colors.cyan}} />
        <div style={{position: 'absolute', right: 10, top: 16, width: 8, height: 8, borderRadius: '50%', background: colors.cyan}} />
        <div style={{position: 'absolute', top: -13, left: 21, width: 3, height: 11, background: colors.amber}} />
        <div style={{position: 'absolute', top: -17, left: 18, width: 9, height: 9, borderRadius: '50%', background: colors.amber}} />
      </div>
      <div style={{position: 'absolute', right: 36, bottom: 58, width: 88, height: 88, borderRadius: 22, border: `2px solid ${colors.mint}`, display: 'flex', alignItems: 'center', justifyContent: 'center', color: colors.mint, fontSize: 16, fontWeight: 900, textAlign: 'center', lineHeight: 1.1, opacity: reveal(frame, 178)}}>WORLD<br />EVENT</div>
      <div style={{position: 'absolute', left: 30, bottom: 24, display: 'flex', gap: 10, opacity: reveal(frame, 202)}}>
        {['SAW', 'ACTED', 'RESULT'].map((item, index) => (
          <div key={item} style={{borderRadius: 999, padding: '9px 13px', border: `1px solid ${[colors.cyan, colors.amber, colors.mint][index]}77`, color: [colors.cyan, colors.amber, colors.mint][index], fontSize: 14, fontWeight: 900, letterSpacing: 1.2}}>{item}</div>
        ))}
      </div>
    </div>
  );
};

const HookSlide = ({duration}: {duration: number}) => {
  const frame = useCurrentFrame();
  const panels = [
    {src: 'opener-labyrinth-overview.png', delay: 0},
    {src: 'opener-rts-bridge.jpg', delay: 6},
    {src: 'opener-conquest-overview.png', delay: 12},
  ];

  return (
    <AbsoluteFill style={{background: colors.ink, color: colors.text, fontFamily: 'Arial, Helvetica, sans-serif'}}>
      <div style={{display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', height: '100%'}}>
        {panels.map((panel, index) => (
          <div
            key={panel.src}
            style={{
              position: 'relative',
              overflow: 'hidden',
              opacity: reveal(frame, panel.delay, 20),
              translate: `0 ${rise(frame, panel.delay, 72)}px`,
              borderRight: index < panels.length - 1 ? `2px solid ${colors.ink}` : undefined,
            }}
          >
            <Img
              src={staticFile(panel.src)}
              style={{
                width: '100%',
                height: '100%',
                objectFit: 'cover',
                scale: interpolate(frame, [0, duration], [1.08, 1.14]),
                filter: 'saturate(0.84) brightness(0.66)',
              }}
            />
          </div>
        ))}
      </div>
      <AbsoluteFill style={{background: 'rgba(3,8,12,0.34)'}} />
      <Vignette strength={0.84} />
      <Brand section="THE QUESTION" slide="01 / 04" />
      <div
        style={{
          position: 'absolute',
          zIndex: 20,
          left: 92,
          right: 92,
          top: 246,
          textAlign: 'center',
          opacity: reveal(frame, 22),
          translate: `0 ${rise(frame, 22, 38)}px`,
        }}
      >
        <div style={{color: colors.cyan, fontSize: 22, fontWeight: 900, letterSpacing: 6}}>WORLDEVAL</div>
        <div style={{marginTop: 24, fontSize: 104, lineHeight: 0.99, fontWeight: 900, letterSpacing: -4.5}}>
          Models can answer.
          <br />
          <span style={{color: colors.mint}}>Can they act?</span>
        </div>
        <div
          style={{
            marginTop: 34,
            fontSize: 29,
            color: colors.text,
            fontWeight: 650,
            opacity: reveal(frame, 122, 26),
            translate: `0 ${rise(frame, 122, 18)}px`,
          }}
        >
          Evaluate what an LLM <span style={{color: colors.amber}}>does</span> inside a world.
        </div>
      </div>
      <WipeIn accent={colors.cyan} />
    </AbsoluteFill>
  );
};

const BehaviourSlide = ({duration}: {duration: number}) => {
  const frame = useCurrentFrame();
  const metrics = ['FINISH', 'RECOVER', 'USE RESOURCES', 'FOLLOW RULES'];

  return (
    <AbsoluteFill style={{background: colors.ink, color: colors.text, fontFamily: 'Arial, Helvetica, sans-serif'}}>
      <Img
        src={staticFile('opener-labyrinth-overview.png')}
        style={{
          position: 'absolute',
          inset: 0,
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          scale: interpolate(frame, [0, duration], [1.05, 1.12]),
          filter: 'brightness(0.23) saturate(0.5) blur(2px)',
        }}
      />
      <AbsoluteFill style={{background: 'linear-gradient(115deg, rgba(3,8,12,0.97), rgba(3,8,12,0.76))'}} />
      <Brand section="LLM BEHAVIOURAL EVALS" slide="02 / 04" />
      <div style={{position: 'absolute', zIndex: 20, left: 76, right: 76, top: 160}}>
        <div style={{fontSize: 74, lineHeight: 1.03, fontWeight: 900, letterSpacing: -3, opacity: reveal(frame, 18)}}>
          The benchmark is the <span style={{color: colors.amber}}>whole run.</span>
        </div>
        <div style={{display: 'grid', gridTemplateColumns: '1fr 0.82fr', gap: 28, marginTop: 46, alignItems: 'stretch'}}>
          <AgentRunVisual />
          <div style={{display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 16}}>
            {[
              ['WHAT IT SAW', 'participant-visible observation', colors.cyan],
              ['WHAT IT DID', 'controller action', colors.amber],
              ['WHAT HAPPENED', 'world event + result', colors.mint],
            ].map(([label, detail, color], index) => (
              <div key={label} style={{borderRadius: 20, padding: '19px 22px', border: `1px solid ${color}77`, background: colors.panelStrong, opacity: reveal(frame, 74 + index * 35), translate: `0 ${rise(frame, 74 + index * 35, 22)}px`}}>
                <div style={{color, fontSize: 16, fontWeight: 900, letterSpacing: 2}}>{label}</div>
                <div style={{marginTop: 10, color: colors.text, fontSize: 23, lineHeight: 1.2, fontWeight: 800}}>{detail}</div>
              </div>
            ))}
          </div>
        </div>
        <div
          style={{
            display: 'flex',
            gap: 13,
            marginTop: 30,
            justifyContent: 'center',
            opacity: reveal(frame, 182),
          }}
        >
          {metrics.map((metric) => (
            <div
              key={metric}
              style={{
                borderRadius: 999,
                padding: '13px 18px',
                border: `1px solid ${colors.line}`,
                background: 'rgba(2,8,12,0.76)',
                color: colors.muted,
                fontSize: 18,
                fontWeight: 900,
                letterSpacing: 1.3,
              }}
            >
              {metric}
            </div>
          ))}
        </div>
      </div>
      <WipeIn accent={colors.amber} />
    </AbsoluteFill>
  );
};

const ControllerNode = ({
  title,
  detail,
  color,
  frame,
  delay,
}: {
  title: string;
  detail: string;
  color: string;
  frame: number;
  delay: number;
}) => (
  <div
    style={{
      flex: 1,
      minHeight: 165,
      borderRadius: 21,
      padding: '22px 20px',
      border: `1px solid ${color}88`,
      background: colors.panelStrong,
      opacity: reveal(frame, delay),
      translate: `0 ${rise(frame, delay, 25)}px`,
    }}
  >
    <div style={{color, fontSize: 19, fontWeight: 900, letterSpacing: 1.8}}>{title}</div>
    <div style={{marginTop: 15, color: colors.muted, fontSize: 20, lineHeight: 1.3, fontWeight: 650}}>{detail}</div>
  </div>
);

const ControllerSlide = ({duration}: {duration: number}) => {
  const frame = useCurrentFrame();
  const nodes = [
    ['OBSERVATION', 'what the agent can see', colors.cyan],
    ['LLM', 'chooses an action', colors.amber],
    ['JSON CONTROLS', 'move · look · interact', colors.purple],
    ['WORLD', 'physics and rules', colors.coral],
    ['RESULT', 'receipt + next view', colors.mint],
  ] as const;

  return (
    <AbsoluteFill style={{background: colors.ink, color: colors.text, fontFamily: 'Arial, Helvetica, sans-serif'}}>
      <Img
        src={staticFile('demo-media/screenshots/portal/timeline.png')}
        style={{
          position: 'absolute',
          inset: 0,
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          scale: interpolate(frame, [0, duration], [1.04, 1.1]),
          filter: 'brightness(0.19) saturate(0.52) blur(2px)',
        }}
      />
      <AbsoluteFill style={{background: 'linear-gradient(180deg, rgba(3,8,12,0.96), rgba(3,8,12,0.8))'}} />
      <Brand section="THE CONTROLLER LOOP" slide="03 / 04" />
      <div style={{position: 'absolute', zIndex: 20, left: 76, right: 76, top: 142}}>
        <div style={{fontSize: 70, lineHeight: 1.03, fontWeight: 900, letterSpacing: -3, opacity: reveal(frame, 22)}}>
          Text becomes <span style={{color: colors.cyan}}>controller input.</span>
        </div>
        <div style={{display: 'flex', alignItems: 'center', gap: 12, marginTop: 55}}>
          {nodes.map(([title, detail, color], index) => (
            <>
              <ControllerNode key={title} title={title} detail={detail} color={color} frame={frame} delay={58 + index * 20} />
              {index < nodes.length - 1 ? <Arrow key={`${title}-arrow`} frame={frame} delay={71 + index * 20} /> : null}
            </>
          ))}
        </div>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1.1fr',
            gap: 22,
            marginTop: 35,
            opacity: reveal(frame, 172),
          }}
        >
          <div
            style={{
              borderRadius: 18,
              padding: '20px 24px',
              background: colors.panelStrong,
              border: `1px solid ${colors.purple}88`,
              color: colors.text,
              fontFamily: 'Menlo, Monaco, monospace',
              fontSize: 23,
              lineHeight: 1.55,
            }}
          >
            {'{ "move_y": 1000, "interact": true }'}
          </div>
          <div
            style={{
              borderRadius: 18,
              padding: '20px 24px',
              background: 'rgba(7, 28, 27, 0.9)',
              border: `1px solid ${colors.mint}66`,
              color: colors.text,
              fontSize: 23,
              fontWeight: 700,
              lineHeight: 1.4,
            }}
          >
            <span style={{color: colors.mint, fontWeight: 900}}>The model chooses the input.</span> The simulation decides what happens.
          </div>
        </div>
      </div>
      <WipeIn accent={colors.cyan} />
    </AbsoluteFill>
  );
};

const CapabilityOrbit = () => {
  const frame = useCurrentFrame();
  const center = 390;
  const rings = [
    {label: 'GPT-3 · LANGUAGE', radius: 104, color: colors.amber, delay: 42, x: 260, y: 278},
    {label: 'GPT-4 · VISION + REASONING', radius: 171, color: colors.cyan, delay: 65, x: 486, y: 230},
    {label: 'GPT-5 · AGENTS + TOOLS', radius: 238, color: colors.mint, delay: 88, x: 170, y: 590},
    {label: 'NEXT · PHYSICAL ACTION', radius: 305, color: colors.text, delay: 111, x: 430, y: 698},
  ];

  return (
    <svg viewBox="0 0 780 780" style={{width: 780, height: 780, overflow: 'visible'}}>
      <defs>
        <radialGradient id="orbitGlow">
          <stop offset="0%" stopColor="#54D9E8" stopOpacity="0.22" />
          <stop offset="72%" stopColor="#071018" stopOpacity="0" />
        </radialGradient>
      </defs>
      <circle cx={center} cy={center} r="365" fill="url(#orbitGlow)" />
      {rings.map((ring) => {
        const circumference = 2 * Math.PI * ring.radius;
        const progress = reveal(frame, ring.delay, 34);
        return (
          <g key={ring.label} opacity={progress}>
            <circle
              cx={center}
              cy={center}
              r={ring.radius}
              fill="none"
              stroke={ring.color}
              strokeWidth={ring.radius === 305 ? 2.8 : 2}
              strokeDasharray={circumference}
              strokeDashoffset={circumference * (1 - progress)}
              opacity={ring.radius === 305 ? 0.82 : 0.64}
            />
            <circle cx={ring.x} cy={ring.y} r="6" fill={ring.color} />
            <text x={ring.x + 14} y={ring.y + 6} fill={ring.color} fontFamily="Arial, Helvetica, sans-serif" fontSize="17" fontWeight="800" letterSpacing="1.2">
              {ring.label}
            </text>
          </g>
        );
      })}
      <circle cx={center} cy={center} r="22" fill={colors.amber} opacity={reveal(frame, 28)} />
      <text x={center} y={center + 52} textAnchor="middle" fill={colors.text} fontFamily="Arial, Helvetica, sans-serif" fontSize="20" fontWeight="900" letterSpacing="3">
        CAPABILITY
      </text>
    </svg>
  );
};

const FrontierSlide = () => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{background: colors.ink, color: colors.text, fontFamily: 'Arial, Helvetica, sans-serif'}}>
      <AbsoluteFill style={{background: 'radial-gradient(circle at 70% 46%, rgba(84,217,232,0.14), transparent 31%), radial-gradient(circle at 16% 90%, rgba(255,195,92,0.12), transparent 28%)'}} />
      <Brand section="THE FRONTIER" slide="04 / 04" />
      <div style={{position: 'absolute', zIndex: 20, left: 84, top: 196, width: 800}}>
        <div style={{color: colors.amber, fontSize: 20, fontWeight: 900, letterSpacing: 4.6, opacity: reveal(frame, 20)}}>
          CAPABILITY IS EXPANDING
        </div>
        <div style={{marginTop: 24, fontSize: 76, lineHeight: 1.02, letterSpacing: -3.4, fontWeight: 900, opacity: reveal(frame, 34)}}>
          Test behaviour
          <br />
          in simulation <span style={{color: colors.mint}}>first.</span>
        </div>
        <div style={{marginTop: 32, width: 690, color: colors.muted, fontSize: 28, lineHeight: 1.4, fontWeight: 620, opacity: reveal(frame, 74)}}>
          Before agents control software or robots, understand how they act under real consequences.
        </div>
        <div
          style={{
            marginTop: 44,
            display: 'inline-flex',
            borderRadius: 17,
            padding: '17px 23px',
            background: colors.cyan,
            color: colors.ink,
            fontSize: 23,
            fontWeight: 900,
            boxShadow: `0 20px 60px ${colors.cyan}44`,
            opacity: reveal(frame, 286),
            translate: `0 ${rise(frame, 286, 22)}px`,
          }}
        >
          NOW, OPEN THE LIVE LAB&nbsp;&nbsp;→
        </div>
      </div>
      <div style={{position: 'absolute', zIndex: 15, right: 52, top: 150, opacity: reveal(frame, 22)}}>
        <CapabilityOrbit />
      </div>
      <WipeIn accent={colors.mint} />
    </AbsoluteFill>
  );
};

const scenes = {
  hook: 14 * FPS,
  behaviour: 16 * FPS,
  controller: 18 * FPS,
  frontier: 17 * FPS,
};

export const WORLD_EVAL_HACKATHON_OPENER_DURATION = Object.values(scenes).reduce((sum, value) => sum + value, 0);

export const WorldEvalHackathonOpener = () => {
  let cursor = 0;
  const sequence = (duration: number, name: string, content: React.ReactNode) => {
    const from = cursor;
    cursor += duration;
    return <Sequence key={name} name={name} from={from} durationInFrames={duration} premountFor={FPS}>{content}</Sequence>;
  };

  return (
    <AbsoluteFill style={{background: colors.ink}}>
      {sequence(scenes.hook, '01 — Models can answer. Can they act?', <HookSlide duration={scenes.hook} />)}
      {sequence(scenes.behaviour, '02 — The whole run', <BehaviourSlide duration={scenes.behaviour} />)}
      {sequence(scenes.controller, '03 — Controller loop', <ControllerSlide duration={scenes.controller} />)}
      {sequence(scenes.frontier, '04 — The physical frontier', <FrontierSlide />)}
    </AbsoluteFill>
  );
};
