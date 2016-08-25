'''@cond

   ooo        ooooo           oooooooooo.             ooooo      ooo
   `88.       .888'           `888'   `Y8b            `888b.     `8'
    888b     d'888   .ooooo.   888      888  .ooooo.   8 `88b.    8   .oooo.
    8 Y88. .P  888  d88' `88b  888      888 d88' `88b  8   `88b.  8  `P  )88b
    8  `888'   888  888   888  888      888 888ooo888  8     `88b.8   .oP"888
    8    Y     888  888   888  888     d88' 888    .o  8       `888  d8(  888
   o8o        o888o `Y8bod8P' o888bood8P'   `Y8bod8P' o8o        `8  `Y888""8o

Copyright
    2014-2016 MoDeNa Consortium, All rights reserved.

License
    This file is part of Modena.

    Modena is free software; you can redistribute it and/or modify it under
    the terms of the GNU General Public License as published by the Free
    Software Foundation, either version 3 of the License, or (at your option)
    any later version.

    Modena is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
    FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
    details.

    You should have received a copy of the GNU General Public License along
    with Modena.  If not, see <http://www.gnu.org/licenses/>.
@endcond'''

"""
@file      Backward mapping FireTask for rheology model.
@author    Christos Mitrias
@copyright 2014-2016, MoDeNa Project. GNU Public License.
@ingroup   app_foaming
"""

import os
import json
import modena
import SurfaceTension
import polymerViscosity 
from modena import ForwardMappingModel, BackwardMappingModel, SurrogateModel, CFunction, ModenaFireTask
import modena.Strategy as Strategy
from fireworks.user_objects.firetasks.script_task import FireTaskBase, ScriptTask
from fireworks import Firework, Workflow, FWAction
from fireworks.utilities.fw_utilities import explicit_serialize
from jinja2 import Template


# ********************************* Class ************************************ #
@explicit_serialize
class RheologyExactTask(ModenaFireTask):
    """
    A FireTask that starts a microscopic code and updates the database.
    """
    def task(self, fw_spec):

        # Write input
        Template('''{{ s['point']['T'] }}
                {{ s['point']['shear'] }}
                {{ s['point']['X'] }}
                {{ s['point']['m0'] }}
                {{ s['point']['m1'] }}
		{{ s['point']['mu'] }}
		{{ s['point']['ST'] }}'''.strip()).stream(s=self).dump('RheologyExact.in')


        # Execute the detailed model
        ret = os.system(os.path.dirname(os.path.abspath(__file__)) + '/src/rheologyexactdummy')
        # This enables backward mapping capabilities (not needed in this example)
        self.handleReturnCode(ret)

        # Analyse output
        f=open('RheologyExact.out','r')
        self['point']['mu_car'] = float(f.readline())
        f.close()

        os.remove('RheologyExact.in')
        os.remove('RheologyExact.out')

with open(os.getcwd()+'/inputs/unifiedInput.json') as jsonfile:
    inputs=json.load(jsonfile)
    X_gel=inputs['kinetics']['gelPoint']

f = CFunction(
    Ccode= '''
#include "modena.h"
#include "math.h"
#include <stdio.h>

void rheology_SM
(
    const modena_model_t* model,
    const double* inputs,
    double *outputs
)
{
    {% block variables %}{% endblock %}

    const double lambda = parameters[0];
    const double alpha = parameters[1];
    const double n_rh = parameters[2];

    const double A_mu = 0.0387; // could be loaded from some library for consistence
    const double E_mu = 10000;
    const double R_rh = 8.314;
    const double mu_a = 1.5;
    const double mu_b = 1;
    const double mu_c = 0;
    const double mu_d = 0.001;
    const double X_gel = '''+str(X_gel)+''';
    const double mu_0_const = 0.195;
    const double mu_inf_const = 0.266;

    double mu_0, mu_inf;
    double mu_car;

    if (X<X_gel) {
        mu_0 = (log(X+mu_d) - log(mu_d) + pow(X_gel / ( X_gel - X ), mu_a + X*mu_b + mu_c*pow(X,2))) * mu_0_const;
        mu_inf = (log(X+mu_d) - log(mu_d) + pow(X_gel / ( X_gel - X ), mu_a + X*mu_b + mu_c*pow(X,2)))* mu_inf_const;

        mu_car = (mu_inf + (mu_0 - mu_inf)*pow(1 + pow(lambda*shear,alpha), (n_rh - 1) / alpha));
    } else {
        mu_car = 1e6;
    }
    //    printf("apparent viscosity car %f", mu_car);
    outputs[0] = mu_car;
}
''',
   # These are global bounds for the function
   inputs={
       'T': {'min': 0, 'max': 9e99 },
       'shear': {'min': 0, 'max': 9e99 },
       'X': {'min': 0, 'max': 1 },
       'm0': {'min': 0, 'max': 9e99 },
       'm1': {'min': 0, 'max': 9e99 },
       'mu': {'min': 0, 'max': 1000 },
       'ST': {'min': 0, 'max': 100 },
   },
   outputs={
       'mu_car': { 'min': 0, 'max': 9e99, 'argPos': 0 },
   },
   parameters={
       'lamdba': { 'min': 1.35, 'max': 21.35, 'argPos': 0 },
       'alpha': { 'min': 0, 'max': 2, 'argPos': 1 },
       'n_rh': { 'min': 0, 'max': 2, 'argPos': 2 },
   },
)

m = BackwardMappingModel(
    _id= 'Rheology',
    surrogateFunction= f,
    exactTask= RheologyExactTask(),
    substituteModels= [ polymerViscosity.m_polymerViscosity, SurfaceTension.m],
#    substituteModels= [ ],
    initialisationStrategy= Strategy.InitialPoints(
        initialPoints=
        {
            'T': [270.0, 330.0], # 330 is the maximum that is supported by Surface Tension Model (Air+THF)
            'shear': [0.01, 0.1],
            'X': [0, 0.3],
            'm0': [1e8, 1e15],
            'm1': [1e-30, 1e5],
        },
    ),
    outOfBoundsStrategy= Strategy.ExtendSpaceStochasticSampling(
        nNewPoints= 4
    ),
    parameterFittingStrategy= Strategy.NonLinFitWithErrorContol(
        testDataPercentage= 0.2,
        maxError= 0.5,
        improveErrorStrategy= Strategy.StochasticSampling(
            nNewPoints= 2
        ),
        maxIterations= 5 # Currently not used
    ),
)
