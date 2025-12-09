#include <Arduino_RouterBridge.h>
#include "detection_frames.h"

extern "C" void matrixWrite(const uint32_t* buf);
extern "C" void matrixBegin();

const int ledPin = LED_BUILTIN;

// Non-blocking animation state
struct AnimationState {
  bool active;
  const uint32_t* const* frames;
  int frameCount;
  int repeatTotal;
  int repeatCurrent;
  int frameIndex;
  unsigned long frameDelay;
  unsigned long lastFrameTime;
} anim = {false, nullptr, 0, 0, 0, 0, 0, 0};

void setLedState(bool state) {
  // LED active LOW : true = ON -> LOW, false = OFF -> HIGH
  digitalWrite(ledPin, state ? LOW : HIGH);

  Monitor.print("[C++] setLedState(");
  Monitor.print(state ? "true" : "false");
  Monitor.println(")");
}

void matrixClear() {
  static const uint32_t empty[4] = {0};
  matrixWrite(empty);
}

void startAnimation(const uint32_t* const frames[], int frameCount, int repeat, int frameDelay) {
  anim.active = true;
  anim.frames = frames;
  anim.frameCount = frameCount;
  anim.repeatTotal = repeat;
  anim.repeatCurrent = 0;
  anim.frameIndex = 0;
  anim.frameDelay = frameDelay;
  anim.lastFrameTime = millis();
  
  // Show first frame immediately
  if (frameCount > 0) {
    matrixWrite(frames[0]);
  }
}

void updateAnimation() {
  if (!anim.active) return;
  
  unsigned long now = millis();
  if (now - anim.lastFrameTime >= anim.frameDelay) {
    anim.lastFrameTime = now;
    anim.frameIndex++;
    
    // Check if we finished all frames in this repeat
    if (anim.frameIndex >= anim.frameCount) {
      anim.frameIndex = 0;
      anim.repeatCurrent++;
      
      // Check if all repeats are done
      if (anim.repeatCurrent >= anim.repeatTotal) {
        anim.active = false;
        // Clear the matrix
        matrixClear();
        return;
      }
    }
    
    // Show current frame
    matrixWrite(anim.frames[anim.frameIndex]);
  }
}

void playAnimation() {
  startAnimation(PersonFrames, PersonFramesCount, 50, 500);
  Monitor.println("[C++] Animation started");
}

void setup() {
  matrixBegin();
  pinMode(ledPin, OUTPUT);

  // LED OFF at startup
  digitalWrite(ledPin, HIGH);

  Bridge.begin();
  Monitor.begin();
  Bridge.provide("setLedState", setLedState);
  Bridge.provide("playAnimation", playAnimation);
  Monitor.println("[C++] setup done, LED is OFF");
}

void loop() {
  updateAnimation();
  delay(10);
}
