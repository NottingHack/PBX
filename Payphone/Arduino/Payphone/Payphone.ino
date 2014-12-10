/*
 * Nottingham Hackspace Payphone firmware
 * 
 * Requires the Keypad library form 
 * http://playground.arduino.cc/uploads/Code/keypad.zip
 *
 * Auth: Matt Lloyd
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2014 Matt Lloyd
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 *
 */

#include <Keypad.h>

#define VERSION 0.1


// key pad setup
const byte rows = 4; //four rows
const byte cols = 3; //three columns
byte rowPins[rows] = {7, 6, 5, 4}; //connect to the row pinouts of the keypad
byte colPins[cols] = {10, 9, 8}; //connect to the column pinouts of the keypad
Keypad keypad = Keypad( makeKeymap(keys), rowPins, colPins, rows, cols );
 
char keys[rows][cols] = {
  {'1','2','3'},
  {'4','5','6'},
  {'7','8','9'},
  {'#','0','*'}
};

#define RING_START 'R'
#define RING_STOP 'r'
#define ON_HOOK 'H'
#define OFF_HOOK 'h'
#define FOLLOW_KEY 'F'

#define HOOK 2
#define FOLLOW 3
#define RINGER 11

int8_t incomming;
volatile uint8_t newHookState = 0;
volatile uint8_t newFollowState = 1;
uint8_t hookState = 0;
uint8_t followState = 1;

void setup()
{
    Serial.begin(9600);
    
//    keypad.addEventListener(keypadEvent);  // Add an event listener.
//    keypad.setHoldTime(500);               // Default is 1000mS
//    keypad.setDebounceTime(250);           // Default is 50mS

    attacheInterrupt(0, hook, CHANGE);
    attacheInterrupt(1, follow, CHANGE);
    
}

void loop()
{
    if (Serial.avaliable()) {
        incomming = Serial.read();
        switch (incommming) {
            case 'R':
                ringStart();
                break;
            case 'r':
                ringStop();
                break:
            default:
                break;
        }
    }
    
    char key = keypad.getKey();

    if (key != NO_KEY){
      Serial.print(key);
    }
 
    if (followState != newFollowState) {
        followState = newFollowState;   
        if (!followState)
            Serial.print(FOLLOW_KEY):
    }
    
    if (hookState != newHookState) {
        hookState = newHookState;
        Serial.print(hookState ? OFF_HOOK : ON_HOOK);
    }    
}

void hook()
{
    newHookState = digitalRead(HOOK);
}

void follow()
{
    newFollowState = digitalRead(FOLLOW);
}

void ringStart()
{
    analogWrite(RINGER,128);
}

void ringStop()
{
    analogWrite(RINGER, 0);
}


/*
void keypadEvent(KeypadEvent key){
  switch (keypad.getState()){
    case PRESSED:
      switch (key){
        case '#': digitalWrite(ledPin,!digitalRead(ledPin)); break;
        case '*': 
          digitalWrite(ledPin,!digitalRead(ledPin));
        break;
      }
      break;
    case RELEASED:
      switch (key){
        case '*': 
          digitalWrite(ledPin,!digitalRead(ledPin));
          blink = false;
        break;
      }
      break;
    case HOLD:
      switch (key){
        case '*': blink = true; break;
      }
      break;
  }
}
*/