/*
  Pen plotter firmware - Arduino Uno, 4x 28BYJ-48 + ULN2003

  Axes: X (1 motor, 2mm pitch, 0-105mm), Y (2 motors, 2mm pitch, 0-166mm),
  Z (1 motor, 1.5mm pitch).

    Motor   IN1  IN2  IN3  IN4
    X       2    4    3    5
    Y1      6    8    7    9
    Y2      10   12   11   13
    Z       A0   A2   A1   A3

  Serial protocol (9600 baud):
    G1 X<mm> Y<mm>      move to position
    M3 / M5             pen down / pen up
    M2                  end job, return to 0,0
    JOGZ<mm>             nudge Z (+ down, - up)
    JOGX<mm> / JOGY<mm> nudge X/Y directly
    SETPOS X<mm> Y<mm>  set current position without moving
    SETDOWN / SETUP     mark current Z as down/up without moving
*/

#include <AccelStepper.h>
#include <MultiStepper.h>

#define X_IN1 2
#define X_IN2 4
#define X_IN3 3
#define X_IN4 5

#define Y1_IN1 6
#define Y1_IN2 8
#define Y1_IN3 7
#define Y1_IN4 9

#define Y2_IN1 10
#define Y2_IN2 12
#define Y2_IN3 11
#define Y2_IN4 13

#define Z_IN1 A0
#define Z_IN2 A2
#define Z_IN3 A1
#define Z_IN4 A3

const float STEPS_PER_REV = 4096.0;

const float X_PITCH_MM = 2.0;
const float Y_PITCH_MM = 2.0;
const float Z_PITCH_MM = 1.5;

const float X_STEPS_PER_MM = STEPS_PER_REV / X_PITCH_MM;
const float Y_STEPS_PER_MM = STEPS_PER_REV / Y_PITCH_MM;
const float Z_STEPS_PER_MM = STEPS_PER_REV / Z_PITCH_MM;

const float X_MAX_MM = 105.0;
const float Y_MAX_MM = 166.0;

const float Z_LIFT_MM = 3.0;
const long  Z_LIFT_STEPS = (long)(Z_LIFT_MM * Z_STEPS_PER_MM);

const float XY_MAX_SPEED = 350.0;
const float Z_MAX_SPEED  = 300.0;

AccelStepper motorX (AccelStepper::HALF4WIRE, X_IN1, X_IN3, X_IN2, X_IN4);
AccelStepper motorY1(AccelStepper::HALF4WIRE, Y1_IN1, Y1_IN3, Y1_IN2, Y1_IN4);
AccelStepper motorY2(AccelStepper::HALF4WIRE, Y2_IN1, Y2_IN3, Y2_IN2, Y2_IN4);
AccelStepper motorZ (AccelStepper::HALF4WIRE, Z_IN1, Z_IN3, Z_IN2, Z_IN4);

MultiStepper xySync;

float curX_mm = 0.0;
float curY_mm = 0.0;
bool penIsDown = true;

void setup() {
  Serial.begin(9600);

  motorX.setMaxSpeed(XY_MAX_SPEED);
  motorY1.setMaxSpeed(XY_MAX_SPEED);
  motorY2.setMaxSpeed(XY_MAX_SPEED);
  motorZ.setMaxSpeed(Z_MAX_SPEED);

  xySync.addStepper(motorX);
  xySync.addStepper(motorY1);
  xySync.addStepper(motorY2);

  Serial.println(F("Pen plotter ready. Assumes pen DOWN at X0 Y0."));
  Serial.println(F("ok"));
}

void loop() {
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.length() > 0) {
      handleCommand(line);
      Serial.println(F("ok"));
    }
  }
}

void handleCommand(String cmd) {
  cmd.toUpperCase();

  if (cmd.startsWith("G1")) {
    float targetX = curX_mm;
    float targetY = curY_mm;

    int xIdx = cmd.indexOf('X');
    int yIdx = cmd.indexOf('Y');

    if (xIdx != -1) targetX = cmd.substring(xIdx + 1).toFloat();
    if (yIdx != -1) targetY = cmd.substring(yIdx + 1).toFloat();

    moveTo(targetX, targetY);
  }
  else if (cmd.startsWith("M3")) {
    penDown();
  }
  else if (cmd.startsWith("M5")) {
    penUp();
  }
  else if (cmd.startsWith("M2")) {
    endJob();
  }
  else if (cmd.startsWith("JOGX")) {
    float jogMm = cmd.substring(4).toFloat();
    long jogSteps = (long)(jogMm * X_STEPS_PER_MM);
    motorX.move(jogSteps);
    runToCompletion(motorX);
    disableXY();
  }
  else if (cmd.startsWith("JOGY")) {
    float jogMm = cmd.substring(4).toFloat();
    long jogSteps = (long)(jogMm * Y_STEPS_PER_MM);
    motorY1.move(jogSteps);
    motorY2.move(jogSteps);
    runToCompletionTwo(motorY1, motorY2);
    disableXY();
  }
  else if (cmd.startsWith("JOGZ")) {
    float jogMm = cmd.substring(3).toFloat();
    long jogSteps = (long)(jogMm * Z_STEPS_PER_MM);
    motorZ.move(jogSteps);
    runToCompletion(motorZ);
    disableZ();
  }
  else if (cmd.startsWith("SETPOS")) {
    int xIdx = cmd.indexOf('X');
    int yIdx = cmd.indexOf('Y');
    if (xIdx != -1) curX_mm = cmd.substring(xIdx + 1).toFloat();
    if (yIdx != -1) curY_mm = cmd.substring(yIdx + 1).toFloat();
    Serial.print(F("Position set to X"));
    Serial.print(curX_mm);
    Serial.print(F(" Y"));
    Serial.println(curY_mm);
  }
  else if (cmd.startsWith("SETDOWN")) {
    penIsDown = true;
    Serial.println(F("Marked current position as pen-down."));
  }
  else if (cmd.startsWith("SETUP")) {
    penIsDown = false;
    Serial.println(F("Marked current position as pen-up."));
  }
  else {
    Serial.print(F("Unknown command: "));
    Serial.println(cmd);
  }
}

void moveTo(float xmm, float ymm) {
  xmm = constrain(xmm, 0.0, X_MAX_MM);
  ymm = constrain(ymm, 0.0, Y_MAX_MM);

  long xSteps = (long)(xmm * X_STEPS_PER_MM);
  long ySteps = (long)(ymm * Y_STEPS_PER_MM);

  long targets[3] = { xSteps, ySteps, ySteps };
  xySync.moveTo(targets);

  while (xySync.run()) {}

  curX_mm = xmm;
  curY_mm = ymm;

  disableXY();
}

void penDown() {
  if (!penIsDown) {
    motorZ.move(Z_LIFT_STEPS);
    runToCompletion(motorZ);
    penIsDown = true;
    disableZ();
  }
}

void penUp() {
  if (penIsDown) {
    motorZ.move(-Z_LIFT_STEPS);
    runToCompletion(motorZ);
    penIsDown = false;
    disableZ();
  }
}

void endJob() {
  penUp();
  moveTo(0.0, 0.0);
  disableXY();
  disableZ();
  Serial.println(F("Job complete. Returned to origin, pen up."));
}

void runToCompletion(AccelStepper &motor) {
  motor.setSpeed(motor.maxSpeed());
  while (motor.distanceToGo() != 0) {
    motor.runSpeedToPosition();
  }
}

void runToCompletionTwo(AccelStepper &m1, AccelStepper &m2) {
  m1.setSpeed(m1.maxSpeed());
  m2.setSpeed(m2.maxSpeed());
  while (m1.distanceToGo() != 0 || m2.distanceToGo() != 0) {
    if (m1.distanceToGo() != 0) m1.runSpeedToPosition();
    if (m2.distanceToGo() != 0) m2.runSpeedToPosition();
  }
}

void disableXY() {
  motorX.disableOutputs();
  motorY1.disableOutputs();
  motorY2.disableOutputs();
}

void disableZ() {
  motorZ.disableOutputs();
}
