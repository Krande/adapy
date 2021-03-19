# Polymers in Finite Elements

## Resources
### General Tips
* https://info.simuleon.com/blog/modelling-hyperelastic-behavior-using-test-data-in-abaqus
* https://www.youtube.com/watch?v=lEh3VTnDmnk
* https://www.researchgate.net/topic/Hyperelastic-Material-Model
### Curve fitting (From Researchgate.net)
As the equation is nonlinear, you cannot use a simple least squares estimator anymore, because the parameters you want to find are not linearly separable.
So if you want to fit measured data to this model, you have a nonlinear least squares problem. There are several ways to solve this problem:
1. This is the simplest one: use the gradient descend method (also known as backpropagation in ANN). This method only finds a local optimal solution (depending on the function and initialization point), so there might be a set of parameters, which fits the problem better. ( https://en.wikipedia.org/wiki/Gradient_descent )
2. You can also use the Levenberg-Marquardt algorithm, which converges faster than the gradient descend method, but also only finds a local optimum ( https://en.wikipedia.org/wiki/Levenberg–Marquardt_algorithm )
3. use a Maximum likehood estimator: https://en.wikipedia.org/wiki/Maximum_likelihood_estimation
4. Use a global optimization algorithm like the particle swarm optimization or an evolutionary algorithm, which can find the global optimal set of parameters with a very high probability. ( https://en.wikipedia.org/wiki/Particle_swarm_optimization )
Your optimization goal for1,2 and 4 is to minimize the residual sum of squares ( https://en.wikipedia.org/wiki/Residual_sum_of_squares ), which is defined by the deviation of the measured data compared to the model output with the actual parameter set.

### Other
Fenicsproject dolfin python wrapper?

## Constitutive Models
* Neo Hookean (isotropic hyperelastic)
* Yeoh (isotropic hyperelastic)
* Bergström-Boyce Model (viscoplastic)

## Type of polymers
* Nylon: Semi Crystalline thermoplastic
* Polyurethane: 


## Neo Hookean
