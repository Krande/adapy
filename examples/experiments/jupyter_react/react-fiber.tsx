import React, { useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import { Mesh } from "three";

export default function() {
    return (
        <div className={"relative flex"}>
            <div className={"absolute w-full h-full"}>
                <Canvas>
                    <OrbitControls />
                    <ambientLight />
                    {/* eslint-disable-next-line react/no-unknown-property */}
                    <pointLight position={[10, 10, 10]} />
                    <Cube />
                </Canvas>
            </div>
        </div>
    );
}

function Cube(props: any) {
    const mesh = useRef<Mesh>(null);

    // const [hovered, setHover] = useState(false);
    // const [active, setActive] = useState(false);
    // const { viewport } = useThree();
    useFrame(() => {
        if (mesh.current) {
            mesh.current.rotation.x = mesh.current.rotation.y += 0.01;
        }
    });
    return (
        <mesh
            {...props}
            ref={mesh}
            // scale={(viewport.width / 5) * (active ? 1.5 : 1)}
            // onClick={() => setActive(!active)}
            // onPointerOver={() => setHover(true)}
            // onPointerOut={() => setHover(false)}
        >
            <boxGeometry />
            <meshStandardMaterial color={"orange"} />
        </mesh>
    );
}
