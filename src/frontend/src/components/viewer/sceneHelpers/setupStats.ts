import Stats from "three/examples/jsm/libs/stats.module";

export function setupStats(
  container: HTMLDivElement,
  showPerf: boolean,
): Stats[] {
  const statsArray: Stats[] = [];

  if (!showPerf) return statsArray;

  for (let i = 0; i < 3; i++) {
    const stats = new Stats();
    stats.showPanel(i);
    container.appendChild(stats.dom);

    Object.assign(stats.dom.style, {
      position: "absolute",
      top: `${i * 50}px`,
      right: "0px",
      left: "auto",
      zIndex: "20",
    });

    statsArray.push(stats);
  }

  return statsArray;
}
