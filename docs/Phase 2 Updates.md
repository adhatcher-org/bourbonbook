# Phase 2 Updates

## Consider RTX 3090 Capacity and Time required to load multiple models during a user transaction

1. Outline the processes in the application that require LLM support and assign which model should be used for each of those functions. Example:
   1. Adding a new bottle
      1. Photo Analysis (Bottle Fill Level, Extract Text from Bottle...)
      2. Bottle Attributes
      3. Pricing Information
   2. Looking up bottle information based on Bottle Name
   3. Updating Pricing information
2. Map out the timing of the calling of the different models to determine if there will be issues with needing to load different models during a process (e.g., loading the photo analysis model before the bottle attributes model).
3. Consider how the application can handle multiple requests simultaneously, such as adding new bottles and looking up information at the same time.
4. Evaluate the potential impact of using RTX 3090 capacity on the performance of the application during a user transaction.
