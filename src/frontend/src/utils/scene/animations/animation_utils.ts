// Global API for controlling animations (e.g., for buttons or UI controls)
import {animationControllerRef} from "../../../state/refs";

const playAnimation = (clipName: string) => {
    animationControllerRef.current?.playAnimation(clipName);
};

const pauseAnimation = () => {
    animationControllerRef.current?.pauseAnimation();
};

const resumeAnimation = () => {
    animationControllerRef.current?.resumeAnimation();
};

const stopAnimation = () => {
    animationControllerRef.current?.stopAnimation();
};