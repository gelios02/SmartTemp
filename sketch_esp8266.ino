#include <OneWire.h>
#include <DallasTemperature.h>
#include "DHT.h" 
#include <Wire.h>
#include <Adafruit_ADS1X15.h>
#include <ESP8266WiFi.h>
#include <PubSubClient.h>

#define DHTPIN 2
#define DHTTYPE DHT11

Adafruit_ADS1115 ads;
// DHT 11
// DHT 22  (AM2302), AM2321
// DHT 21 (AM2301)

// обьявляем обьект dht с параметрами
DHT dht(DHTPIN, DHTTYPE);

// Data wire is connected to GPIO 0
#define ONE_WIRE_BUS 0

OneWire oneWire(ONE_WIRE_BUS);
DallasTemperature sensors(&oneWire);

const char* ssid = "TP-Link_29C4";
const char* password = "~Jakergtn1";
const char* mqtt_server = "192.168.1.81"; 
const int mqtt_port = 1883;

WiFiClient espClient;
PubSubClient client(espClient);
String clientId = "ESP-" + String(random(0xffff), HEX);

void setup(void) {
  Serial.begin(115200);
  delay(10);
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(",");
  }
  Serial.println("WiFi connected");
  pinMode(ONE_WIRE_BUS, INPUT);
  pinMode(DHTPIN, INPUT);  
  sensors.begin();
  dht.begin(); // запускаем датчик
  //Wire.begin(D1, D2);
  ads.setGain(GAIN_ONE);
  ads.begin();  
  client.setServer(mqtt_server, mqtt_port);
  client.setKeepAlive(60);
}

void reconnect() {
  while (!client.connected()) {
    if (client.connect("arduinoClient")) {
      Serial.println("MQTT connected");
    } else {
      Serial.print("failed, rc=");
      Serial.print(client.state());  // Код ошибки
      Serial.println(" try again in 5s");
      delay(5000);
    }
  }
}

void loop(void) { 
  if (!client.connected()) reconnect();
  client.loop();

 sensors.requestTemperatures(); 
 
 delay(750); 
 
 float tempC = sensors.getTempCByIndex(0);
 Serial.print("Temperature: ");
 Serial.print(tempC);
 Serial.println("°C");
 float Humidity = dht.readHumidity(); 
 float temp2 = dht.readTemperature(); 

  // проверяем полученные значения
  if (isnan(Humidity)) {
    Serial.println("Ошибка чтения датчика");
    return;
  }
 
 
  // выводим полученные данные в консоль	
  Serial.print("Влажность : ");
  Serial.println(Humidity);
  

  float adc0, adc1;

  adc0 = float(ads.readADC_SingleEnded(0))* 0.125/ 1000.0; 
  adc1 = float(ads.readADC_SingleEnded(1))* 0.125/ 1000.0; 
  
  //adc_01_voltage = ads.readADC_Differential_0_1();   // Чтение значения с аналогового входа 0
  float q = (adc1-adc0)*20.16 ;  
  
  
  Serial.print("ADC0: "); Serial.println(adc0);  // Вывод значений на последовательный порт
  Serial.print("ADC1: "); Serial.println(adc1);
  // Отправка данных
  client.publish("sensors/temperature", String(tempC).c_str());
  client.publish("sensors/humidity", String(Humidity).c_str());
  client.publish("sensors/thermal", String(q).c_str());

   delay(5000);

   } 
   