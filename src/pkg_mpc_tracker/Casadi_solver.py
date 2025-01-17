import sys
import numpy as np
import os
import json
import pathlib

import casadi as cs # type: ignore
from pkg_mpc_tracker.direct_multiple_shooting import MultipleShootingSolver
from pkg_mpc_tracker.build import mpc_cost




class CasadiSolver:
    def __init__(self, config, robot_specification, params, initial_guess):
        # Define control parameters 
        self.config = config
        self.robot_spec = robot_specification
        self.ns = self.config.ns      #Noumber of states
        self.nu = self.config.nu     #Noumber of inputs
        self.N = self.config.N_hor   #Control horizon
        self.ts = self.config.ts    #Sample time
        self.params = params

        #Test
        self.initial_guess = initial_guess

        ### State bounds of the robot
        self.lin_vel_min = self.robot_spec.lin_vel_min   # Vehicle contraint on the minimal velocity possible
        self.lin_vel_max = self.robot_spec.lin_vel_max   # Vehicle contraint on the maximal velocity possible
        self.ang_vel_min = -self.robot_spec.ang_vel_max  # Vehicle contraint on the maximal angular velocity
        self.ang_vel_max = self.robot_spec.ang_vel_max   # Vehicle contraint on the maximal angular velocity

        # Initial states
        self.x0 = params[2:5]
        self.ref_state = params[5:8]
        
    def get_map_data(self):
        """Gets the map data from the according json file

        Returns:
            List: List of lists containing boundary coordinates
        """
        ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]
        DATA_DIR = os.path.join(ROOT_DIR, "data/test_data")
        map_path = os.path.join(DATA_DIR, "map.json")

        map_data = json.load(open(map_path))
               
        return map_data

    def get_state_bounds(self):
        """Gets the map data from the according json file

        Returns:
            List: List of lists containing boundary coordinates
        """
        ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]
        DATA_DIR = os.path.join(ROOT_DIR,"data/test_data")
        map_path = os.path.join(DATA_DIR, "map.json")

        map_data = json.load(open(map_path))
               
        return map_data['boundary_coords']
        
    def unicycle_model(self, state: cs.SX, action: cs.SX, ts: float, rk4:bool=True) -> cs.SX:
        """Unicycle model.
        
        Args:
            ts: Sampling time.
            state: x, y, and theta.
            action: speed and angular speed.
            rk4: If True, use Runge-Kutta 4 to refine the model.
        """
        def d_state_f(state, action):
            return ts * cs.vertcat(action[0]*cs.cos(state[2]), action[0]*cs.sin(state[2]), action[1])
            
        if rk4:
            k1 = d_state_f(state, action)
            k2 = d_state_f(state + 0.5*k1, action)
            k3 = d_state_f(state + 0.5*k2, action)
            k4 = d_state_f(state + k3, action)
            d_state = (1/6) * (k1 + 2*k2 + 2*k3 + k4)
        else:
            d_state = d_state(state, action)

        return state + d_state
    
    def return_continuous_function(self) -> cs.Function:
        
        x = cs.SX.sym('x', self.ns)
        u = cs.SX.sym('u', self.nu)
        f = self.unicycle_model(x, u, self.ts)
       
        fk = cs.Function('fk', [x,u], [f])
        return fk
    
    def individual_cost(self, Cost_dict: dict, varialbes: cs.SX, state: list, input: list):

        #Get a list of 3 states and 2 inputs for N
        merge_list = state[:3]
        for i in range(self.N):
            merge_list += input[2*(i):2*(i)+2]
            merge_list += state[3*(i+1):3*(i+1)+3]

        #Calc individual costs
        for key, value in Cost_dict.items():
            cost_ind = cs.Function(key,[varialbes],[value])
            print(key,' : ', cost_ind(merge_list))
        #ref_path_cost = cs.Function('test',[varialbes],[Cost_dict['ref_path']])
        #print('ref_path',ref_path_cost(merge_list))
    

    def run(self):
        """Run the Casadi solver for the entire horizon

        Returns:
            Any: A list of optimal control inputs, calulated total cost, 
            the exit status of the solver and the time it took to run the solver
        """
        # Define a symbolic continious function
        fk = self.return_continuous_function()

        # Discretize the continious function 
        ms_solver = MultipleShootingSolver(self.ns, self.nu, self.ts, self.N, self.config,self.robot_spec,self.initial_guess)
        ms_solver.set_initial_state(self.x0)
        ms_solver.set_motion_model(self.unicycle_model,c2d=False)
        ms_solver.set_parameters(self.params)
        
        map = self.get_map_data()
        bounds = map['boundary_coords']
        # Define state and output bounds
        ms_solver.set_control_bound([self.lin_vel_min, self.ang_vel_min], [self.lin_vel_max, self.ang_vel_max])
        ms_solver.set_state_bound([[bounds[0][0],bounds[0][1],-2*cs.pi]]*(self.N+1), 
                                [[bounds[2][0],bounds[2][1],2*cs.pi]]*(self.N+1))
        ms_solver.set_stcobs_constraints(map['obstacle_list'])
    
        
        problem, Cost_dict =  ms_solver.build_problem()

        sol, solver_time, exit_status, solver_cost = ms_solver.solve()
        print('Total Cost: ', solver_cost)
     
        u_out_nest = ms_solver.get_opt_controls(sol) #Returns 2 lists with each input for the horizon
        x_pred_nest = ms_solver.get_pred_states(sol)

      # Get the nested list down to a single list representing the input vector
        u_out = [u_out_nest[j][i] for i in range(len(u_out_nest[0])) for j in range(len(u_out_nest))] # Merging columns instead of rows for each step in N

        #Calculate individual costs
        x_pred = [x_pred_nest[j][i] for i in range(len(x_pred_nest[0])) for j in range(len(x_pred_nest))]
        #self.individual_cost(Cost_dict, problem['x'], x_pred, u_out)

        #Get the next intial guess list
        initial_guess = []
        for i in range(0, len(x_pred), self.ns):
            initial_guess.extend(x_pred[i:i+self.ns])
            if i // self.ns < len(u_out):
                initial_guess.extend(u_out[i//self.ns * self.nu : i//self.ns * self.nu + self.nu])
        initial_guess = initial_guess[5:]
        initial_guess.extend(initial_guess[-5:])

        
        return u_out, solver_cost, exit_status, solver_time, initial_guess




