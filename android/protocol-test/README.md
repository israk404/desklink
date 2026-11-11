# Protocol test

Runs the Android app's network code on a desktop JVM against the real PC app.

```bash
# 1. start the PC app offscreen in test mode (prints URI, PCSHA and CAPTURES lines)
python windows/tests/serve_for_jvm.py > /tmp/srv.log &

# 2. compile and run the test (needs kotlinc 2.x)
cd android/protocol-test
SRC=../app/src/main/java/com/farhan/desklink
kotlinc $SRC/Protocol.kt $SRC/Connection.kt $SRC/PairClient.kt $SRC/Transfers.kt JvmTest.kt \
  -include-runtime -d jvmtest.jar
java -jar jvmtest.jar "$(grep ^URI /tmp/srv.log | cut -d' ' -f2)" \
  "$(grep ^PCSHA /tmp/srv.log | cut -d' ' -f2)" "$(grep ^CAPTURES /tmp/srv.log | cut -d' ' -f2)"
```

It checks pairing (code match, Allow, Deny), connecting, the camera request, sending a photo to the PC,
receiving a file from the PC with SHA-256, and wrong-key rejection.
