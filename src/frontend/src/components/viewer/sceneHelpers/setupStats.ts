import Stats from "three/examples/jsm/libs/stats.module.js";

export function setupStats(
  container: HTMLDivElement,
): {
  statsArray: Stats[];
  callsPanel: Stats.Panel | null;
  trisPanel: Stats.Panel | null;
} {
  const statsArray: Stats[] = [];
  let callsPanel: Stats.Panel | null = null;
  let trisPanel: Stats.Panel | null = null;


  // — built-in panels: fps (0), ms (1), mb (2)
  for (let i = 0; i < 3; i++) {
    const stats = new Stats();
    stats.showPanel(i);
    container.appendChild(stats.dom);
    Object.assign(stats.dom.style, {
      position: "absolute",
      top: `${i * 48}px`,
      left: "auto",
      right: "0px",
      zIndex: "20",
    });
    statsArray.push(stats);
  }

  // — custom “calls” panel
  {
    const stats = new Stats();
    callsPanel = stats.addPanel(new Stats.Panel("calls", "#ff8", "#221"));
    stats.showPanel(3);
    container.appendChild(stats.dom);
    Object.assign(stats.dom.style, {
      position: "absolute",
      top: `${3 * 48}px`,
      left: "auto",
      right: "0px",
      zIndex: "20",
    });
    statsArray.push(stats);
  }

  // — custom “tris” panel
  {
    const stats = new Stats();
    trisPanel = stats.addPanel(new Stats.Panel("tris", "#8ff", "#122"));
    stats.showPanel(3);
    container.appendChild(stats.dom);
    Object.assign(stats.dom.style, {
      position: "absolute",
      top: `${4 * 48}px`,
      left: "auto",
      right: "0px",
      zIndex: "20",
    });
    statsArray.push(stats);
  }

  return { statsArray, callsPanel, trisPanel };
}
