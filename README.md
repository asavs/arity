i want to makke my own harness. i keep trying but it gets bloated. it, i believe could be under 1,000 lines. I have a few main goals besides having a small number of lines,
▎ which is kinda like a lazy thing that people say when they want to understand the code and/or not spend all day writing lol. anyway #1 is iw ant to be able to run models in
▎ parallel or superparallel. so if i ask a request, maybe that same request can get sent to 3 different models who will all come up with their answer. #2 is i want to do some
▎ natural recursion over that. models that are running in parallel get, overseen by more models running in parallel. its like llm-as-a-judge i guess. i think models in agent
▎ swarms refer to this as "the classifier" or "the scorer". ideally they aren't super anxious or worried about that. how/why? #3 models can see and communicate with eachother
▎ openly. maybe after just 1 independent run on a task. so 3 models get the task, make a button, they all mak ehte button, and then the scorer looks at all the buttons, and
▎ somehow we synthesize the best button maybe by cherry picking on the part of the scorer or the human looking it over, or maybe we open up the floor and let the 3 contestants
▎ talk to eachother and bounce ideas off and then compromise to produce a final singular button. which leads me to #4, we are running a lot of runtimes so we need to be very
▎ minimal and memory concientious, and perhaps look into multiplexers or smolvm or something creative. #5 agent personas and memories, built out of the grades via "the scorer"
▎ over time #6 automatic model/persona pairings by task analysis #7 a nice ui where i can easily communicate and visualize all the different models working (ideally extremely
▎ minimalistic and terminal based)
