"""
  Copyright (c) 2011-2013 Michael E. Ferguson. All right reserved.

  This program is free software; you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation; either version 2 of the License, or
  (at your option) any later version.

  This program is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.

  You should have received a copy of the GNU General Public License
  along with this program; if not, write to the Free Software Foundation,
  Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
"""

from __future__ import print_function

import rospy, actionlib


from tf.listener import *

from geometry_msgs.msg import *
from moveit_msgs.msg import *
from shape_msgs.msg import *

## @brief Utility for moving arm to a pose or joint state
class ArmInterface:

    ## @brief Constructor for this utility
    ## @param group Name of the MoveIt! group to command
    ## @param frame Name of the fixed frame in which planning happens
    ## @param listener A TF listener instance (optional, will create a new one if None)
    ## @param plan_only Should we only plan, but not execute?
    def __init__(self, group, frame, listener = None, plan_only = False):
        self._group = group
        self._fixed_frame = frame
        self._action = actionlib.SimpleActionClient('move_group', MoveGroupAction)
        self._action.wait_for_server()
        if listener == None:
            self._listener = TransformListener()
        else:
            self._listener = listener
        self.plan_only = plan_only

    def moveToJointPosition(self, joints, positions, tolerance = 0.01, start_state = None):
        g = MoveGroupGoal()
        # 1. fill in workspace_parameters
        # 2. fill in start_state
        if start_state:
            g.request.start_state = start_state
        # 3. fill in goal_constraints
        c1 = Constraints()
        for i in range(len(joints)):
            c1.joint_constraints.append(JointConstraint())
            c1.joint_constraints[i].joint_name = joints[i]
            c1.joint_constraints[i].position = positions[i]
            c1.joint_constraints[i].tolerance_above = tolerance
            c1.joint_constraints[i].tolerance_below = tolerance
            c1.joint_constraints[i].weight = 1.0
        g.request.goal_constraints.append(c1)
        # 4. fill in path constraints
        # 5. fill in trajectory constraints
        # 6. fill in planner id
        # 7. fill in group name
        g.request.group_name = self._group
        # 8. fill in number of planning attempts
        g.request.num_planning_attempts = 1
        # 9. fill in allowed planning time
        g.request.allowed_planning_time = 15.0
        # TODO: fill in
        # g.planning_options.planning_scene_diff.allowed_collision_matrix
        g.planning_options.plan_only = self.plan_only
        g.planning_options.look_around = False
        g.planning_options.replan = False
        self._action.send_goal(g)
        self._action.wait_for_result()
        return self._action.get_result()

    def moveToPose(self, pose_stamped, gripper_frame, tolerance = 0.01):
        """ Move the arm, based on a goal pose_stamped for the end effector. """
        g = MoveGroupGoal()
        pose_transformed = self._listener.transformPose(self._fixed_frame, pose_stamped)

        # 1. fill in workspace_parameters
        # 2. fill in start_state
        # 3. fill in goal_constraints
        c1 = Constraints()

        c1.position_constraints.append(PositionConstraint())
        c1.position_constraints[0].header.frame_id = self._fixed_frame
        c1.position_constraints[0].link_name = gripper_frame
        b = BoundingVolume()
        s = SolidPrimitive()
        s.dimensions = [tolerance * tolerance]
        s.type = s.SPHERE
        b.primitives.append(s)
        b.primitive_poses.append(pose_transformed.pose)
        c1.position_constraints[0].constraint_region = b
        c1.position_constraints[0].weight = 1.0

        c1.orientation_constraints.append(OrientationConstraint())
        c1.orientation_constraints[0].header.frame_id = self._fixed_frame
        c1.orientation_constraints[0].orientation = pose_transformed.pose.orientation
        c1.orientation_constraints[0].link_name = gripper_frame
        c1.orientation_constraints[0].absolute_x_axis_tolerance = tolerance
        c1.orientation_constraints[0].absolute_y_axis_tolerance = tolerance
        c1.orientation_constraints[0].absolute_z_axis_tolerance = tolerance
        c1.orientation_constraints[0].weight = 1.0

        g.request.goal_constraints.append(c1)

        # 4. fill in path constraints
        # 5. fill in trajectory constraints
        # 6. fill in planner id
        # 7. fill in group name
        g.request.group_name = self._group
        # 8. fill in number of planning attempts
        g.request.num_planning_attempts = 1
        # 9. fill in allowed planning time
        g.request.allowed_planning_time = 15.0
        # TODO: fill in
        # g.planning_options.planning_scene_diff.allowed_collision_matrix
        g.planning_options.plan_only = self.plan_only
        g.planning_options.look_around = False
        g.planning_options.replan = False

        self._action.send_goal(g)
        self._action.wait_for_result()
        return self._action.get_result()
