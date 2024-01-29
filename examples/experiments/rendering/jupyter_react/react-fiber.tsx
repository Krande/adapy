import React, {useRef, useState} from "react"
import {Canvas, useFrame, useThree} from 'https://esm.sh/@react-three/fiber@8.13.0'
import {OrbitControls} from "https://esm.sh/@react-three/drei@9.68.6";

function Box({position, color}) {
    const ref = useRef()
    useFrame(() => {
        if (ref.current) (
            (ref.current.rotation.x = ref.current.rotation.y += 0.01)
        )

    })

    return (
        <mesh position={position} ref={ref}>
            <boxBufferGeometry args={[1, 1, 1]} attach="geometry"/>
            <meshPhongMaterial color={color} attach="material"/>
        </mesh>
    )
}

function App() {
    return (
        <>
            <OrbitControls/>
            <Canvas>
                <Box color="#18a36e" position={[-1, 0, 3]}/>
                <Box color="#f56f42" position={[1, 0, 3]}/>

                <directionalLight color="#ffffff" intensity={1} position={[-1, 2, 4]}/>
            </Canvas>
        </>
    )
}

export default App