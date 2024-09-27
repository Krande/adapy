// src/types/3dconnexion.d.ts

declare global {
  interface Window {
    _3Dconnexion: any;
    mouseX: number;
    mouseY: number;
  }
}

interface _3DconnexionInstance {
  connect: () => boolean;
  create3dmouse: (canvas: HTMLElement, name: string) => void;
  update3dcontroller: (data: any) => void;
}

declare var _3Dconnexion: {
  Action: any;
  ActionSet: any;
  ImageCache: any;
  ImageItem: any;
  Category: any;
  ActionTree: any;
  new (glInstance: any): _3DconnexionInstance;
};
