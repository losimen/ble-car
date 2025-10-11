String G_Bluetooth_value;
volatile int BLE_Change_SPEED;

void setup(){
  Serial.begin(9600);
  G_Bluetooth_value = "";
  BLE_Change_SPEED = 60; // Default speed value
  pinMode(2, OUTPUT);    // Left motor direction control
  pinMode(5, OUTPUT);    // Left motor PWM (speed)
  pinMode(4, OUTPUT);    // Right motor direction control
  pinMode(6, OUTPUT);    // Right motor PWM (speed)
}

void loop(){
  // Read all incoming serial data into G_Bluetooth_value
  while (Serial.available() > 0) {
    G_Bluetooth_value += (char)Serial.read();
    delay(2);
  }
  
  if (G_Bluetooth_value.length() > 0) {
    Serial.println(G_Bluetooth_value);
    int len = G_Bluetooth_value.length();
    
    // Process only commands starting with '%'
    if (G_Bluetooth_value.charAt(0) == '%') {
      char cmd;
      // Check if a numeric value is provided before the command letter.
      if (len > 2 && isDigit(G_Bluetooth_value.charAt(1))) {
        // Assume the command letter is the last character
        String numStr = G_Bluetooth_value.substring(1, len - 1);
        BLE_Change_SPEED = numStr.toInt();
        cmd = G_Bluetooth_value.charAt(len - 1);
      } else {
        // Otherwise, the command letter is at index 1.
        cmd = G_Bluetooth_value.charAt(1);
      }
      
      // Calculate the motor speed using the same formula used for L and R.
      float motorSpeed = (BLE_Change_SPEED / 10.0) * 22.5;
      
      switch(cmd) {
        case '+':  // Speed adjustment command (update already done above)
          Serial.print("Speed updated to: ");
          Serial.println(BLE_Change_SPEED);
          break;
          
        case '-':  // Speed adjustment command (update already done above)
          Serial.print("Speed updated to: ");
          Serial.println(BLE_Change_SPEED);
          break;
          
        case 'L':  // Turn Left
          // For turning, you may want to use differential speeds.
          digitalWrite(2, LOW);
          analogWrite(5, motorSpeed);      // Left motor at computed speed (or adjust as needed)
          digitalWrite(4, LOW);
          analogWrite(6, motorSpeed);      // Right motor at computed speed (or adjust as needed)
          break;
          
        case 'R':  // Turn Right
          digitalWrite(2, HIGH);
          analogWrite(5, motorSpeed);
          digitalWrite(4, HIGH);
          analogWrite(6, motorSpeed);
          break;
          
        case 'W':  // Move Forward
          digitalWrite(2, HIGH);   // Left motor forward
          analogWrite(5, motorSpeed);
          digitalWrite(4, LOW);    // Right motor forward
          analogWrite(6, motorSpeed);
          break;
          
        case 'S':  // Move Backward
          digitalWrite(2, LOW);    // Left motor reverse
          analogWrite(5, motorSpeed);
          digitalWrite(4, HIGH);   // Right motor reverse
          analogWrite(6, motorSpeed);
          break;
          
        default:   // Unrecognized command: stop the motors.
          digitalWrite(2, LOW);
          analogWrite(5, 0);
          digitalWrite(4, HIGH);
          analogWrite(6, 0);
          break;
      }
    } else {
      // If the command doesn't start with '%', stop the motors.
      digitalWrite(2, LOW);
      analogWrite(5, 0);
      digitalWrite(4, HIGH);
      analogWrite(6, 0);
    }
    
    // Clear the command buffer after processing.
    G_Bluetooth_value = "";
  }
}

