import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls";

interface InternalOrbitControls extends OrbitControls {
  rotateLeft: (angle: number) => void;
  rotateUp: (angle: number) => void;
  panLeft: (distance: number, objectMatrix: THREE.Matrix4) => void;
  panUp: (distance: number, objectMatrix: THREE.Matrix4) => void;
  panOffset: THREE.Vector3;
  sphericalDelta: THREE.Spherical;
  scale: number;
  EPS: number;
  getAutoRotationAngle: () => number;
}

export class ZUpOrbitControls extends OrbitControls {
  private _zUpQuat: THREE.Quaternion;
  private _zUpQuatInverse: THREE.Quaternion;

  constructor(object: THREE.Camera, domElement?: HTMLElement) {
    super(object, domElement);

    this._zUpQuat = new THREE.Quaternion().setFromUnitVectors(object.up, new THREE.Vector3(0, 0, 1));
    this._zUpQuatInverse = this._zUpQuat.clone().invert();

    // Delay-patch the update AFTER super() finishes
    const originalUpdate = this.update.bind(this);
    this.update = () => {
      const controls = this as unknown as InternalOrbitControls;

      // 🛡 skip if sphericalDelta not initialized yet
      if (!controls.sphericalDelta) {
        return originalUpdate();
      }

      const offset = new THREE.Vector3();
      const position = this.object.position;

      offset.copy(position).sub(this.target);

      offset.applyQuaternion(this._zUpQuat);

      const spherical = new THREE.Spherical();
      spherical.setFromVector3(offset);

      if (this.autoRotate && this.enableRotate) {
        controls.rotateLeft(controls.getAutoRotationAngle());
      }

      spherical.theta += controls.sphericalDelta.theta;
      spherical.phi += controls.sphericalDelta.phi;

      spherical.phi = Math.max(this.minPolarAngle, Math.min(this.maxPolarAngle, spherical.phi));
      spherical.makeSafe();

      spherical.radius *= controls.scale;
      spherical.radius = Math.max(this.minDistance, Math.min(this.maxDistance, spherical.radius));

      this.target.add(controls.panOffset);

      offset.setFromSpherical(spherical);
      offset.applyQuaternion(this._zUpQuatInverse);

      position.copy(this.target).add(offset);

      this.object.lookAt(this.target);

      if (this.enableDamping) {
        controls.sphericalDelta.theta *= 1 - this.dampingFactor;
        controls.sphericalDelta.phi *= 1 - this.dampingFactor;
        controls.panOffset.multiplyScalar(1 - this.dampingFactor);
      } else {
        controls.sphericalDelta.set(0, 0, 0);
        controls.panOffset.set(0, 0, 0);
      }

      controls.scale = 1;

      this.dispatchEvent({ type: 'change' });

      return true;
    };
  }
}
