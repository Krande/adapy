import React, {useEffect} from 'react';
import vtkFullScreenRenderWindow from 'vtk.js/Sources/Rendering/Misc/FullScreenRenderWindow';
import vtkActor from 'vtk.js/Sources/Rendering/Core/Actor';
import vtkMapper from 'vtk.js/Sources/Rendering/Core/Mapper';
import vtkConeSource from "vtk.js/Sources/Filters/Sources/ConeSource";

function VTKRenderer() {
    useEffect(() => {
        const fullScreenRenderer = vtkFullScreenRenderWindow.newInstance();
        const renderer = fullScreenRenderer.getRenderer();
        const renderWindow = fullScreenRenderer.getRenderWindow();

        // Create a sphere
        const coneSource = vtkConeSource.newInstance();
        const mapper = vtkMapper.newInstance();
        mapper.setInputConnection(coneSource.getOutputPort());

        const actor = vtkActor.newInstance();
        actor.setMapper(mapper);

        renderer.addActor(actor);
        renderer.resetCamera();
        renderWindow.render();
    }, []);

    return <div className={"h-screen w-full h-full"} />;
}

export default VTKRenderer;
