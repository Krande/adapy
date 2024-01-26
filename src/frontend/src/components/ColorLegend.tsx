import React from 'react';
import {useColorStore} from '../state/colorLegendStore';

const ColorLegend = () => {
    const {min, max, step, minColor, maxColor} = useColorStore();


    const values = [];
    // min, max can be floats 0.0-1.0, and length of values should always be equal to steps
    for (let i = 0; i <= step; i++) {
        values.push(min + i * (max - min) / step);
    }
    console.log('values', values)
    console.log('minValue', min)
    console.log('maxValue', max)
    console.log('step', step)
    console.log('minColor', minColor)
    console.log('maxColor', maxColor)

    const gradientStyle = {
        backgroundImage: `linear-gradient(to top, ${minColor}, ${maxColor})`,
        zIndex: 1000,
    };

    return (
        <div className="flex">
            <div className="flex flex-col justify-between h-64">
                {values.map(value => <div key={value}>{value}</div>)}
            </div>
            <div className="min-w-80 h-64 mx-4" style={gradientStyle}>ColorLegend</div>
        </div>
    );
};

export default ColorLegend;