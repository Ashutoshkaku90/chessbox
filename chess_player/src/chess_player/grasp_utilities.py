#!/usr/bin/env python

""" 
  Copyright (c) 2013 Michael E. Ferguson. All right reserved.

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

import thread, copy, math
import rospy
import actionlib
from math import sqrt

from tf.listener import *
from tf.transformations import euler_from_quaternion, quaternion_from_euler

from chess_msgs.msg import ChessPiece
from sensor_msgs.msg import JointState
from geometry_msgs.msg import PoseStamped, Pose, PoseArray
from shape_msgs.msg import SolidPrimitive
from moveit_msgs.msg import PickupAction, PickupGoal, PlaceAction, PlaceGoal, MoveGroupAction, MoveGroupGoal
from moveit_msgs.msg import Constraints, JointConstraint, PositionConstraint, OrientationConstraint
from moveit_msgs.msg import AttachedCollisionObject, CollisionObject, PlanningScene
from manipulation_msgs.msg import Grasp, GripperTranslation, PlaceLocation

from chess_utilities import SQUARE_SIZE, castling_extras

# The frame used for approach and retreat translations, gripper_link is local
#   so approach/translation gets transformed by the grasp orientation
GRIPPER_FRAME = 'gripper_link'

# The frame that all objects/poses should be translated to, the frame in which
#   moveit planning is done
FIXED_FRAME = 'base_link'

# Should we only plan, and not execute?
PLAN_ONLY = False

# This was previously 0.0075
GRIPPER_CLOSED = 0.01
GRIPPER_OPEN = 0.05

# Tucking the arm requires a set of joint constraints
joint_names = ['arm_lift_joint', 'arm_shoulder_pan_joint', 'arm_upperarm_roll_joint', 'arm_shoulder_lift_joint', 'arm_elbow_flex_joint', 'arm_wrist_flex_joint', 'arm_wrist_roll_joint']
joints_tucked  = [0.0, -1.57, 0.0, -1.7, 1.7, 1.57, -0.066472500808377785]
joints_untucked  = [0.0, 0.0, 0.0, -1.57, 1.57, 1.57, -0.066472500808377785]

# TODO: This is currently quite the hack. The simple_moveit_plugin will use the
#       js.position[0] as the input to the gripper (which is interpreted as how
#       wide to open the gripper, not the joint angle). Should either figure out
#       how to hide *_gripper_joint from moveit (preferred) or how to pass the
#       values more correctly at least.
def getGripperPosture(pose):
    """ This is Maxwell-specific. """
    js = JointState()
    js.name = ['l_gripper_joint', 'r_gripper_joint']
    js.position = [pose, pose]
    js.velocity = [0.0, 0.0]
    js.effort = [1.0, 1.0]
    return js

def getGripperTranslation(min_dist, desired, axis=1.0):
    gt = GripperTranslation()
    gt.direction.vector.x = axis
    gt.direction.header.frame_id = GRIPPER_FRAME
    gt.min_distance = min_dist
    gt.desired_distance = desired
    return gt

# TODO: all this generator crap was created before changing approach frame to
#       gripper_link -- at some point, should see if we can remove it all and just
#       pass a list of grasps to moveit (previously, it crashed with an Eigen error
#       and I just didn't have time to fix it)
def getGrasps(pose_stamped):
    """ Returns an iterator of increasingly worse grasps. """
    g = Grasp()
    # directly overhead first
    g.id = 'direct_overhead'
    g.pre_grasp_posture = getGripperPosture(GRIPPER_OPEN)
    g.grasp_posture = getGripperPosture(GRIPPER_CLOSED)
    g.grasp_pose = pose_stamped
    q = quaternion_from_euler(0, 1.57, 0)
    g.grasp_pose.pose.orientation.x = q[0]
    g.grasp_pose.pose.orientation.y = q[1]
    g.grasp_pose.pose.orientation.z = q[2]
    g.grasp_pose.pose.orientation.w = q[3]
    g.grasp_quality = 1.0
    g.approach = getGripperTranslation(0.05, 0.15)
    g.retreat = getGripperTranslation(0.05, 0.15, -1.0)
    #g.max_contact_force =
    #g.allowed_touch_objects[] =
    yield g
    # now tilt the hand a bit, and rotate about yaw
    for p in [0.05, 0.1, 0.2, 0.4]:
        for y in [-1.57, -0.78, 0.0, 0.78, 1.57]:
            q = quaternion_from_euler(0, 1.57-p, y)
            g.grasp_pose.pose.orientation.x = q[0]
            g.grasp_pose.pose.orientation.y = q[1]
            g.grasp_pose.pose.orientation.z = q[2]
            g.grasp_pose.pose.orientation.w = q[3]
            g.id = str(p) + '+' + str(y)
            g.grasp_quality = 1.0 - (1.25 * p) - abs(y)/4.0
            #pub.publish(pa)
            yield g

def getPlaceLocations(pose_stamped):
    """ Returns an iterator of increasingly worse place locations. """
    l = PlaceLocation()
    # directly overhead first
    l.id = 'direct_overhead'
    l.place_pose = pose_stamped
    q = quaternion_from_euler(0, 1.57, 0)
    l.place_pose.pose.orientation.x = q[0]
    l.place_pose.pose.orientation.y = q[1]
    l.place_pose.pose.orientation.z = q[2]
    l.place_pose.pose.orientation.w = q[3]
    l.approach = getGripperTranslation(0.05, 0.15)
    l.retreat = getGripperTranslation(0.05, 0.15, -1.0)
    l.post_place_posture = getGripperPosture(GRIPPER_OPEN)
    yield l
    # now tilt the hand a bit, and rotate about yaw
    for p in [0.05, 0.1, 0.2, 0.4]:
        for y in [-1.57, -0.78, 0.0, 0.78, 1.57]:
            q = quaternion_from_euler(0, 1.57-p, y)
            l.place_pose.pose.orientation.x = q[0]
            l.place_pose.pose.orientation.y = q[1]
            l.place_pose.pose.orientation.z = q[2]
            l.place_pose.pose.orientation.w = q[3]
            l.id = str(p) + '+' + str(y)
            yield l

class PickupManager:
    """ This class enables a pick action. """

    def __init__(self, group, ee):
        self._group = group
        self._effector = ee
        self._action = actionlib.SimpleActionClient('pickup', PickupAction)
        self._action.wait_for_server()

    def pickup(self, name, pose_stamped):
        """ This will try to pick up a chess piece. """
        i = 1
        for grasp in getGrasps(pose_stamped):
            g = PickupGoal()
            g.target_name = name
            g.group_name = self._group
            g.end_effector = self._effector
            g.possible_grasps = [grasp]
            g.support_surface_name = "table"
            g.allow_gripper_support_collision = True
            g.attached_object_touch_links = list() # empty list = use all links of end-effector
            #g.path_constraints = ??
            #g.allowed_touch_objects = ['part']
            g.allowed_planning_time = 30.0
            #g.planning_options.planning_scene_diff = ??
            g.planning_options.plan_only = PLAN_ONLY
            self._action.send_goal(g)
            self._action.wait_for_result()
            if self._action.get_result().error_code.val == 1:
                rospy.loginfo("Pick succeeded")
                return True
            rospy.loginfo("Failed Pick attempt %d" % i)
            i += 1
        return False

class PlaceManager:
    """ This class enables a place action. """

    def __init__(self, group, ee):
        self._group = group
        self._effector = ee
        self._action = actionlib.SimpleActionClient('place', PlaceAction)
        self._action.wait_for_server()

    def place(self, name, pose_stamped):
        i = 1
        for location in getPlaceLocations(pose_stamped):
            g = PlaceGoal()
            g.group_name = self._group
            g.attached_object_name = name
            g.place_locations = [location]
            g.support_surface_name = "table"
            g.allow_gripper_support_collision = True
            #g.path_constraints = ??
            #g.allowed_touch_objects = ['part']
            g.allowed_planning_time = 30.0
            #g.planning_options.planning_scene_diff = ??
            g.planning_options.plan_only = PLAN_ONLY
            self._action.send_goal(g)
            self._action.wait_for_result()
            if self._action.get_result().error_code.val == 1:
                rospy.loginfo("Place succeeded")
                return True
            rospy.loginfo("Failed place attempt %d" % i)
            i += 1
        return False

class MotionManager:
    """ This class is used for generic motion planning requests. """

    def __init__(self, group, frame, listener = None):
        self._group = group
        self._fixed_frame = frame
        self._action = actionlib.SimpleActionClient('move_group', MoveGroupAction)
        self._action.wait_for_server()
        if listener == None:
            self._listener = TransformListener()
        else:
            self._listener = listener

    def moveToJointPosition(self, joints, positions, tolerance = 0.01):
        g = MoveGroupGoal()
        # 1. fill in workspace_parameters
        # 2. fill in start_state
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
        g.planning_options.plan_only = PLAN_ONLY
        g.planning_options.look_around = False
        g.planning_options.replan = False
        self._action.send_goal(g)
        self._action.wait_for_result()
        return self._action.get_result()

    def moveToPose(self, pose_stamped):
        """ Move the arm, based on a goal pose_stamped for the end effector. """
        g = MoveGroupGoal()
        pose_transformed = self.listener.transformPose(self._fixed_frame, pose_stamped)

        # 1. fill in workspace_parameters
        # 2. fill in start_state
        # 3. fill in goal_constraints
        c1 = Constraints()

        c1.position_constraints.append(PositionConstraint())
        c1.position_constraints[0].header.frame_id = self._fixed_frame
        c1.position_constraints[0].link_name = GRIPPER_FRAME
        b = BoundingVolume()
        s = SolidPrimitive()
        s.dimensions = [0.0001]
        s.type = s.SPHERE
        b.primitives.append(s)
        b.primitive_poses.append(goal_transformed.pose)
        c1.position_constraints[0].constraint_region = b
        c1.position_constraints[0].weight = 1.0

        c1.orientation_constraints.append(OrientationConstraint())
        c1.orientation_constraints[0].header.frame_id = self._fixed_frame
        c1.orientation_constraints[0].orientation = goal_transformed.pose.orientation
        c1.orientation_constraints[0].link_name = GRIPPER_FRAME
        c1.orientation_constraints[0].absolute_x_axis_tolerance = 1.0
        c1.orientation_constraints[0].absolute_y_axis_tolerance = 1.0
        c1.orientation_constraints[0].absolute_z_axis_tolerance = 0.5
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
        g.planning_options.plan_only = PLAN_ONLY
        g.planning_options.look_around = False
        g.planning_options.replan = False

        self._action.send_goal(g)
        self._action.wait_for_result()
        return self._action.get_result()

# TODO: Add a 'mass' add function
class ObjectManager:
    def __init__(self, frame):
        self._fixed_frame = frame

        # publisher to send objects
        self._pub = rospy.Publisher('collision_object', CollisionObject)
        self._attached_pub = rospy.Publisher('attached_collision_object', AttachedCollisionObject)

        # subscribe to planning scene, track the attached and collision objects
        self._mutex = thread.allocate_lock()
        self._attached = list()
        self._collision = list()
        rospy.Subscriber('move_group/monitored_planning_scene', PlanningScene, self.sceneCb)

    def addBox(self, name, size_x, size_y, size_z, x, y, z):
        o = CollisionObject()
        o.header.stamp = rospy.Time.now()
        o.header.frame_id = self._fixed_frame
        o.id = name

        s = SolidPrimitive()
        s.dimensions = [size_x, size_y, size_z]
        s.type = s.BOX
        o.primitives.append(s)

        ps = PoseStamped()
        ps.header.frame_id = self._fixed_frame
        ps.pose.position.x = x
        ps.pose.position.y = y
        ps.pose.position.z = z
        ps.pose.orientation.w = 1.0
        o.primitive_poses.append(ps.pose)

        o.operation = o.ADD

        self._pub.publish(o)
        while not name in self.getKnownCollisionObjects():
            rospy.logdebug('Waiting for object to add')
            self._pub.publish(o)
            rospy.sleep(1.0)

    def addCube(self, name, size, x, y, z):
        self.addBox(name, size, size, size, x, y, z)

    def remove(self, name):
        """ Remove a an object. """
        o = CollisionObject()
        o.header.stamp = rospy.Time.now()
        o.header.frame_id = self._fixed_frame
        o.id = name
        o.operation = o.REMOVE

        self._pub.publish(o)
        while name in self.getKnownCollisionObjects():
            rospy.logdebug('Waiting for object to remove')
            self._pub.publish(o)
            rospy.sleep(1.0)

    def sceneCb(self, msg):
        """ Recieve updates from move_group. """
        self._mutex.acquire()
        for obj in msg.world.collision_objects:
            try:
                if obj.operation == obj.ADD:
                    self._collision.append(obj.id)
                    rospy.logdebug('ObjectManager: Added Collision Object "%s"' % obj.id)
                elif obj.operation == obj.REMOVE:
                    self._collision.remove(obj.id)
                    rospy.logdebug('ObjectManager: Removed Collision Object "%s"' % obj.id)
            except ValueError:
                pass
        self._attached = list()
        for obj in msg.robot_state.attached_collision_objects:
            rospy.logdebug('ObjectManager: attached collision objects includes "%s"' % obj.object.id)
            self._attached.append(obj.object.id)
        self._mutex.release()

    def getKnownCollisionObjects(self):
        self._mutex.acquire()
        l = copy.deepcopy(self._collision)
        self._mutex.release()
        return l

    def getKnownAttachedObjects(self):
        self._mutex.acquire()
        l = copy.deepcopy(self._attached)
        self._mutex.release()
        return l

class ArmPlanner:
    _group = 'Arm'
    _gripper_group = 'Gripper'

    BOARD_THICKNESS = 0.1
    CHESS_BOARD_FRAME = 'chess_board'

    """ Chess-specific stuff """
    def __init__(self, listener = None):
        self._pick = PickupManager(self._group, self._gripper_group)
        self._place = PlaceManager(self._group, self._gripper_group)
        self._obj = ObjectManager(FIXED_FRAME)
        self._listener = listener
        if self._listener == None:
            self._listener = TransformListener()
        self._move = MotionManager(self._group, FIXED_FRAME, self._listener)
        self.success = True

    def move_piece(self, start_pose, end_pose):
        # update table
        rospy.loginfo('Updating table position')
        self._obj.remove('table')
        p = PoseStamped()
        # back date header.stamp as perception can be slow
        p.header.stamp = rospy.Time.now() - rospy.Duration(1.0)
        p.header.frame_id = self.CHESS_BOARD_FRAME
        p.pose.position.x = SQUARE_SIZE * 4
        p.pose.position.y = SQUARE_SIZE * 4
        p.pose.position.z = -self.BOARD_THICKNESS/2.0
        p.pose.orientation.x = p.pose.orientation.y = p.pose.orientation.z = 0.0
        p.pose.orientation.w = 1.0
        pt = self._listener.transformPose(FIXED_FRAME, p)
        self._obj.addBox('table', SQUARE_SIZE * 8, SQUARE_SIZE * 8, self.BOARD_THICKNESS,
                         pt.pose.position.x, pt.pose.position.y, pt.pose.position.z)
        # remove piece for good measure
        rospy.loginfo('Updating piece position')
        self._obj.remove('piece')
        # insert piece
        p.header.stamp = rospy.Time.now() - rospy.Duration(1.0)
        p.pose.position.x = start_pose.position.x
        p.pose.position.y = start_pose.position.y
        p.pose.position.z = 0.03
        pt = self._listener.transformPose(FIXED_FRAME, p)
        self._obj.addCube('piece', 0.015, pt.pose.position.x, pt.pose.position.y, pt.pose.position.z)
        #self.untuck()
        # pick it up
        rospy.loginfo('Picking piece')
        if not self._pick.pickup('piece', pt):
            return False
        rospy.sleep(1.0)
        # put it down
        rospy.loginfo('Placing piece')
        p.header.stamp = rospy.Time.now() - rospy.Duration(1.0)
        p.pose.position.x = end_pose.position.x
        p.pose.position.y = end_pose.position.y
        p.pose.position.z = 0.03
        pt = self._listener.transformPose(FIXED_FRAME, p)
        self._place.place('piece', pt)
        return True

    def execute(self, move, board):
        """ Execute a move. """

        # get info about move
        (col_f, rank_f) = board.toPosition(move[0:2])
        (col_t, rank_t) = board.toPosition(move[2:])
        fr = board.getPiece(col_f, rank_f)
        to = board.getPiece(col_t, rank_t)

        # is this a capture?
        if to != None:
            off_board = ChessPiece()
            off_board.header.frame_id = fr.header.frame_id
            off_board.pose.position.x = -2 * SQUARE_SIZE
            off_board.pose.position.y = SQUARE_SIZE
            off_board.pose.position.z = fr.pose.position.z
            if not self.move_piece(to.pose, off_board.pose):
                rospy.logerr('Failed to move captured piece')
                self.success = False
                self.tuck()
                return None

        to = ChessPiece()
        to.header.frame_id = fr.header.frame_id
        to.pose = self.getPose(col_t, rank_t, board, fr.pose.position.z)
        if not self.move_piece(fr.pose, to.pose):
            rospy.logerr('Failed to move my piece')
            self.success = False
            self.tuck()
            return None

        if move in castling_extras:
            if not self.execute(castling_extras[move],board):
                rospy.logerr('Failed to carry out castling extra')

        self.tuck()
        return to.pose

    def getPose(self, col, rank, board, z=0):
        """ Find the reach required to get to a position """
        p = Pose()
        if board.side == board.WHITE:
            p.position.x = (col * SQUARE_SIZE) + SQUARE_SIZE/2
            p.position.y = ((rank-1) * SQUARE_SIZE) + SQUARE_SIZE/2
            p.position.z = z
        else:
            p.position.x = ((7-col) * SQUARE_SIZE) + SQUARE_SIZE/2
            p.position.y = ((8-rank) * SQUARE_SIZE) + SQUARE_SIZE/2
            p.position.z = z
        return p

    # TODO: can we kill this?
    def getReach(self, col, rank, board):
        """ Find the reach required to get to a position """
        ps = PoseStamped()
        ps.header.frame_id = self.CHESS_BOARD_FRAME
        ps.pose = self.getPose(board.getColIdx(col), int(rank), board)
        pose = self._listener.transformPose('arm_link', ps)
        x = pose.pose.position.x
        y = pose.pose.position.y
        reach = sqrt( (x*x) + (y*y) )
        return reach

    def tuck(self):
        self._move.moveToJointPosition(joint_names, joints_tucked)

    def untuck(self):
        self._move.moveToJointPosition(joint_names, joints_untucked)

if __name__=='__main__':
    rospy.init_node('grasp_utilities')
    pick = PickupManager('Arm', 'Gripper')
    place = PlaceManager('Arm', 'Gripper')
    obj = ObjectManager(FIXED_FRAME)
    listener = TransformListener()
    move = MotionManager('Arm', FIXED_FRAME, listener)
    # need time for listener to get data
    rospy.sleep(3.0)

    ####################################################
    # this code will display the grasp pose array
    if 0:
        pub = rospy.Publisher('grasp_poses', PoseArray)
        pa = PoseArray()
        pa.header.frame_id = FIXED_FRAME
        rospy.sleep(3.0)
        p = PoseStamped()
        p.header.stamp = rospy.Time.now() - rospy.Duration(1.0)
        p.header.frame_id = 'chess_board'
        p.pose.position.x = SQUARE_SIZE * (0.5 + 4)
        p.pose.position.y = SQUARE_SIZE * (0.5 + 1)
        p.pose.position.z = 0.03
        q = quaternion_from_euler(0.0, 1.57, 0.0)
        p.pose.orientation.x = q[0]
        p.pose.orientation.y = q[1]
        p.pose.orientation.z = q[2]
        p.pose.orientation.w = q[3]
        p_transformed = listener.transformPose(FIXED_FRAME, p)
        for g in getGrasps(p_transformed):
            pa.header.stamp = rospy.Time.now()
            pa.poses.append(copy.deepcopy(g.grasp_pose.pose))
            print("adding pose")
            print(g.grasp_pose.pose)
        pa.header.stamp = rospy.Time.now()
        pub.publish(pa)

    ####################################################
    # this was the original testing code
    if 0:
        # remove old pieces/table if any
        obj.remove('part')
        obj.remove('table')
        for y in [0,1,6,7]:
            for x in range(8):
                obj.remove(chr(97+x)+str(y+1))

    if 0:
        # add table
        p = PoseStamped()
        p.header.stamp = rospy.Time.now() - rospy.Duration(1.0)
        p.header.frame_id = 'chess_board'
        p.pose.position.x = SQUARE_SIZE * 4
        p.pose.position.y = SQUARE_SIZE * 4
        p.pose.position.z = -0.05
        q = quaternion_from_euler(0.0, 0, 0)
        p.pose.orientation.x = q[0]
        p.pose.orientation.y = q[1]
        p.pose.orientation.z = q[2]
        p.pose.orientation.w = q[3]
        p_transformed = listener.transformPose(FIXED_FRAME, p)

        obj.addBox('table', 0.05715 * 8, 0.05715 * 8, .1, p_transformed.pose.position.x, p_transformed.pose.position.y, p_transformed.pose.position.z)
        p.pose.position.z = 0

    if 0:
        # add pieces
        for y in [0,1,6,7]:
            for x in range(8):
                p.header.stamp = rospy.Time.now() - rospy.Duration(1.0)
                p.pose.position.x = SQUARE_SIZE*(0.5+x)
                p.pose.position.y = SQUARE_SIZE*(0.5+y)
                p.pose.position.z = 0.03
                p_transformed = listener.transformPose(FIXED_FRAME, p)
                obj.addCube(chr(97+x)+str(y+1), 0.015, p_transformed.pose.position.x, p_transformed.pose.position.y, p_transformed.pose.position.z)

    if 0:
        # manipulate a part
        p.header.stamp = rospy.Time.now() - rospy.Duration(1.0)
        p.pose.position.x = SQUARE_SIZE * (0.5 + 4)
        p.pose.position.y = SQUARE_SIZE * (0.5 + 1)
        p_transformed = listener.transformPose(FIXED_FRAME, p)

        pick.pickup('e2', p_transformed)
        rospy.sleep(1.0)

        p_transformed.pose.position.x += SQUARE_SIZE*2
        place.place('e2', p_transformed)
