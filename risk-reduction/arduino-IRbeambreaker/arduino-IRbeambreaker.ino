#define LED 6
#define DET 8

int THRESH;

void setup() {
  // put your setup code here, to run once:
  pinMode(LED, OUTPUT);
  pinMode(DET, INPUT);
  Serial.begin(9600);
  THRESH = 1023 * 0.01;
}

void loop() {
  // put your main code here, to run repeatedly:
  int result = analogRead(DET);
  if (result <= THRESH) {
    digitalWrite(LED, HIGH);
  } else {
    digitalWrite(LED, LOW);
  }
  // Serial.println(result);
  delay(50);
  // digitalWrite(LED, LOW);
  // delay(1000);
  
}
